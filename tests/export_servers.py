"""Install generated adapters in disposable real servers. Run on an isolated Docker executor."""

import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
WP_IMAGE = os.environ.get("SMW_WORDPRESS_IMAGE", "wordpress:7.1.4-php8.3-apache")
PLUGIN_VERSION = os.environ.get("SMW_REDIRECTION_VERSION", "5.10.1")
TOOLS = ROOT / ".test-tools" / "export-servers"
TOOLS.mkdir(parents=True, exist_ok=True)
uid = secrets.token_hex(5)
network = "smw-" + uid
names = []
results = {}


def run(*args, check=True):
    result = subprocess.run(args, text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(result.stdout[-3000:] + result.stderr[-3000:])
    return result.stdout


def docker(*args, **kwargs):
    return run("docker", *args, **kwargs)


def container(name, image, *args):
    real = "smw-" + uid + "-" + name
    docker(
        "run",
        "-d",
        "--name",
        real,
        "--network",
        network,
        "--network-alias",
        name,
        "--memory",
        "768m",
        "--cpus",
        "1",
        *args,
        image,
    )
    names.append(real)
    return real


def generate(origin, adapter):
    script = r"""
import json, tempfile
from pathlib import Path
from smw.domain import parse_inventory
from smw.store import Store
from smw.exporters import deployment
origin=ORIGIN
with tempfile.TemporaryDirectory() as temp:
 store=Store(Path(temp)/"fixture.sqlite")
 pid=store.create("Real adapter fixture",{"production":origin})
 paths=["/old","/permanent308","/merge-a","/merge-b","/gone"]
 store.import_rows(pid,"old",parse_inventory(("\n".join(origin+p for p in paths)).encode()),"Original synthetic corpus","synthetic")
 changes=[]
 for row in store.inventory(pid):
  path=row["url"].removeprefix(origin)
  decision="retire" if path=="/gone" else "merge"
  status=410 if path=="/gone" else 308 if path=="/permanent308" else 301
  changes.append({"id":row["id"],"patch":{"disposition":decision,"target":origin+"/new" if decision!="retire" else "","status":status,"reviewed":True,"rationale":"Original synthetic adapter fixture"}})
 store.edit(pid,changes,1)
 revision=store.revision(store.freeze(pid)["id"])
 print(deployment(revision,ADAPTER)[0],end="")
""".replace("ORIGIN", repr(origin)).replace("ADAPTER", repr(adapter))
    return docker(
        "run",
        "--rm",
        "--network",
        "none",
        "-v",
        str(ROOT) + ":/work",
        "-w",
        "/work",
        "python:3.12-slim",
        "python",
        "-c",
        script,
    )


def observe(host):
    script = r"""
import http.client,json,time
host=HOST
for attempt in range(50):
 try:
  c=http.client.HTTPConnection(host,timeout=2);c.request("GET","/new");r=c.getresponse();r.read();c.close();break
 except OSError:time.sleep(.2)
rows=[]
for path in ["/old","/permanent308","/merge-a","/merge-b","/gone","/old?token=x","/OLD","/old/","/%6fld","//old","/a/../old"]:
 c=http.client.HTTPConnection(host,timeout=5);c.request("GET",path);r=c.getresponse();rows.append({"path":path,"status":r.status,"location":r.getheader("Location")});r.read();c.close()
print(json.dumps(rows))
""".replace("HOST", repr(host))
    return json.loads(docker("run", "--rm", "--network", network, "python:3.12-slim", "python", "-c", script))


try:
    docker("network", "create", "--internal", network)
    nginx_dir = TOOLS / ("nginx-" + uid)
    nginx_dir.mkdir()
    (nginx_dir / "migration.conf").write_text(generate("http://nginx", "nginx"))
    (nginx_dir / "default.conf").write_text(
        "server { listen 80; server_name nginx; include /fixture/migration.conf; location = /new { return 200 'fixture'; } }"
    )
    nginx = container(
        "nginx",
        "nginx:1.28-alpine",
        "-v",
        str(nginx_dir) + ":/fixture:ro",
        "-v",
        str(nginx_dir / "default.conf") + ":/etc/nginx/conf.d/default.conf:ro",
    )
    ng = observe("nginx")
    for row in ng[:5]:
        expected = 410 if row["path"] == "/gone" else 308 if row["path"] == "/permanent308" else 301
        assert row["status"] == expected, row
        if expected in (301, 308):
            assert row["location"] == "http://nginx/new", row
    assert ng[5]["status"] == 400, ng[5]
    assert all(row["status"] == 404 for row in ng[6:]), ng[6:]
    results["nginx"] = {"image": "nginx:1.28-alpine", "observations": ng, "passed": True}

    wp_dir = TOOLS / ("wordpress-" + uid)
    wp_dir.mkdir()
    plugins = wp_dir / "wp-content" / "plugins"
    plugins.mkdir(parents=True)
    with ZipFile(ROOT / (".test-tools/downloads/redirection-" + PLUGIN_VERSION + ".zip")) as archive:
        for name in archive.namelist():
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError("Unsafe plugin archive")
        archive.extractall(plugins)
    (wp_dir / "migration.json").write_text(generate("http://wp", "wordpress"))
    (wp_dir / ".htaccess").write_text(
        "<IfModule mod_rewrite.c>\nRewriteEngine On\nRewriteBase /\nRewriteRule ^index\\.php$ - [L]\nRewriteCond %{REQUEST_FILENAME} !-f\nRewriteCond %{REQUEST_FILENAME} !-d\nRewriteRule . /index.php [L]\n</IfModule>\n"
    )
    setup = r"""<?php
define('WP_INSTALLING', true);
define('WP_ADMIN', true);
define('WP_DEBUG', true);
$_SERVER['HTTP_HOST']='wp';
require '/var/www/html/wp-load.php';
require_once ABSPATH.'wp-admin/includes/upgrade.php';
require_once ABSPATH.'wp-admin/includes/plugin.php';
if (!is_blog_installed()) wp_install('Original synthetic fixture','fixture_operator','fixture@example.invalid',false,'',bin2hex(random_bytes(16)));
update_option('siteurl','http://wp');
update_option('home','http://wp');
update_option('permalink_structure','/%postname%/');
activate_plugin('redirection/redirection.php');
require_once ABSPATH.'wp-content/plugins/redirection/redirection.php';
if (file_exists(ABSPATH.'wp-content/plugins/redirection/database/database.php')) {
 require_once ABSPATH.'wp-content/plugins/redirection/database/database.php';
 $db=Red_Database::get_latest_database();
} else { $db=\Redirection\Database\Database::get_latest_database(); }
$db->install();
if (function_exists('red_set_options')) { red_set_options(['database'=>REDIRECTION_DB_VERSION]); } else { Red_Options::save(['database'=>REDIRECTION_DB_VERSION]); }
wp_insert_post(['post_type'=>'page','post_title'=>'New fixture','post_name'=>'new','post_content'=>'Original synthetic destination','post_status'=>'publish']);
if (file_exists(ABSPATH.'wp-content/plugins/redirection/models/file-io.php')) {
 require_once ABSPATH.'wp-content/plugins/redirection/models/file-io.php';
 require_once ABSPATH.'wp-content/plugins/redirection/fileio/json.php';
 $importer=new Red_Json_File();
 $count=$importer->load(1,'migration.json',file_get_contents('/var/www/html/migration.json'));
} else {
 $importer=new \Redirection\ImportExport\Format\Json();
 $group=new \Redirection\ImportExport\ImportGroup(0);
 $redirect=new \Redirection\ImportExport\ImportRedirect();
 $importer->load_from_string($group,$redirect,file_get_contents('/var/www/html/migration.json'),false);
 $count=$redirect->get_created();
}
echo json_encode(['imported'=>$count,'version'=>get_bloginfo('version')]);
"""
    (wp_dir / "setup-fixture.php").write_text(setup)
    password = secrets.token_urlsafe(
        24
    )  # Disposable fixture database only, never printed or stored in source.
    db = container(
        "db",
        "mariadb:11.8",
        "-e",
        "MARIADB_ROOT_PASSWORD=" + password,
        "-e",
        "MARIADB_DATABASE=fixture",
        "-e",
        "MARIADB_USER=fixture",
        "-e",
        "MARIADB_PASSWORD=" + password,
    )
    wp = container(
        "wp",
        WP_IMAGE,
        "-e",
        "WORDPRESS_DB_HOST=db",
        "-e",
        "WORDPRESS_DB_USER=fixture",
        "-e",
        "WORDPRESS_DB_PASSWORD=" + password,
        "-e",
        "WORDPRESS_DB_NAME=fixture",
        "-v",
        str(wp_dir) + ":/var/www/html",
    )
    setup_result = ""
    setup_error = ""
    for attempt in range(60):
        try:
            setup_result = docker("exec", wp, "php", "/var/www/html/setup-fixture.php")
            if '"imported":5' in setup_result:
                break
            time.sleep(0.5)
        except RuntimeError as exc:
            setup_error = str(exc)
            time.sleep(0.5)
    assert '"imported":5' in setup_result, setup_error or setup_result
    observations = observe("wp")
    for row in observations[:5]:
        expected = 410 if row["path"] == "/gone" else 308 if row["path"] == "/permanent308" else 301
        assert row["status"] == expected, row
        if expected in (301, 308):
            assert row["location"] == "http://wp/new", row
    assert observations[5]["status"] == 404, observations[5]
    assert observations[6]["status"] == 404 and observations[7]["status"] == 404, observations[6:]
    roundtrip = r"""<?php
define('WP_ADMIN',true);
$_SERVER['HTTP_HOST']='wp';
require '/var/www/html/wp-load.php';
if (file_exists(ABSPATH.'wp-content/plugins/redirection/models/file-io.php')) {
 require_once ABSPATH.'wp-content/plugins/redirection/models/file-io.php';
 require_once ABSPATH.'wp-content/plugins/redirection/fileio/json.php';
 $io=new Red_Json_File();
} else { $io=new \Redirection\ImportExport\Format\Json(); }
$items=Red_Item::get_all();
$groups=Red_Group::get_all();
$payload=$io->get_data($items,$groups);
foreach($items as $item){$item->delete();}
if ($io instanceof Red_Json_File) { $count=$io->load(1,'roundtrip.json',$payload); }
else {
 $group=new \Redirection\ImportExport\ImportGroup(0);
 $redirect=new \Redirection\ImportExport\ImportRedirect();
 $io->load_from_string($group,$redirect,$payload,false);
 $count=$redirect->get_created();
}
echo json_encode(['reimported'=>$count]);
"""
    roundtrip_file = wp_dir.parent / (wp_dir.name + "-roundtrip.php")
    roundtrip_file.write_text(roundtrip)
    docker("cp", str(roundtrip_file), wp + ":/tmp/roundtrip-fixture.php")
    reimported = docker("exec", wp, "php", "/tmp/roundtrip-fixture.php")
    assert '"reimported":5' in reimported, reimported
    repeated = observe("wp")
    assert repeated == observations, (repeated, observations)
    results["wordpress"] = {
        "image": WP_IMAGE,
        "redirection": PLUGIN_VERSION,
        "import": setup_result,
        "observations": observations,
        "roundtrip": "native Redirection export, delete, reimport, identical real HTTP observations",
        "passed": True,
    }
    print(json.dumps(results, indent=2))
finally:
    (ROOT / "evidence" / "export-servers.json").write_text(json.dumps(results, indent=2))
    for name in names:
        diagnostic = docker(
            "inspect", "--format", "{{.State.Status}} {{.State.OOMKilled}}", name, check=False
        )
        if "wordpress" not in results:
            print("Fixture container state:", name, diagnostic)
            log = subprocess.run(["docker", "logs", "--tail", "20", name], text=True, capture_output=True)
            print(log.stdout + log.stderr)
    for name in reversed(names):
        docker("rm", "-fv", name, check=False)
    docker("network", "rm", network, check=False)
