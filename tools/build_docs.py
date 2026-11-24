#!/usr/bin/env python3
"""Strict standalone docs build plus explicit legacy URL compatibility pages.

Run with the interpreter containing requirements-docs.txt. No platform import,
data/model access or deployment is performed. Legacy source bytes are immutable.
"""
import argparse
import gzip
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


class Anchors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key == 'id' and value:
                self.ids.add(value)


def page_for(site, route):
    if not (route.startswith('/') and route.endswith('/') and '..' not in Path(route).parts):
        raise ValueError('invalid documentation route')
    return site / route.lstrip('/') / 'index.html'


def ids(path):
    parser = Anchors()
    parser.feed(path.read_text())
    return sorted(parser.ids)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site-dir', type=Path, default=ROOT / 'site')
    args = parser.parse_args()
    site = args.site_dir.absolute()
    if site == ROOT or site.is_relative_to(ROOT / 'docs'):
        parser.error('site directory must be outside the documentation source')
    if site.resolve() != site or site.is_symlink():
        parser.error('site directory must be canonical')
    marker = site / '.olav-docs-build'
    if site.exists() and any(site.iterdir()):
        if not (marker.is_file() and marker.read_text() == 'olav-skills-docs\n'):
            parser.error('refusing to clean an unrelated nonempty output directory')
    provenance = json.loads((ROOT / 'provenance/docs-legacy-20261117.json').read_text())
    for entry in provenance['files']:
        if hashlib.sha256((ROOT / entry['destination']).read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('legacy snapshot changed: ' + entry['destination'])
    try:
        subprocess.run([sys.executable, '-B', '-m', 'mkdocs', 'build', '--strict', '--config-file', str(ROOT / 'mkdocs.yml'), '--site-dir', str(site)], check=True)
    finally:
        # Permit a retry after a strict-build failure without ever adopting an
        # unrelated pre-existing directory for MkDocs' clean operation.
        site.mkdir(parents=True, exist_ok=True)
        marker.write_text('olav-skills-docs\n')
    mapping = json.loads((ROOT / 'docs-url-map.json').read_text())['routes']
    aliases = []
    reused = []
    permanent = {
        '/quickstart/': '/installation/',
        '/zh/quickstart/': '/zh/installation/',
        '/query/': '/offline-data/',
        '/zh/query/': '/zh/offline-data/',
    }
    for destination in permanent.values():
        if not page_for(site, destination).is_file():
            raise ValueError('missing permanent redirect target: ' + destination)
    for row in mapping:
        target_page = page_for(site, row['new'])
        if not target_page.is_file():
            raise ValueError('missing current target: ' + row['new'])
        legacy = row['legacy_url']
        legacy_page = page_for(site, legacy)
        if not legacy_page.is_file():
            raise ValueError('missing legacy target: ' + legacy)
        target_ids = ids(target_page)
        legacy_ids = ids(legacy_page)
        if not row['needs_redirect']:
            script = ('let fragment;try{fragment=decodeURIComponent(location.hash.slice(1));}'
                      'catch{fragment=location.hash.slice(1);}'
                      f'const current={json.dumps(target_ids)};const historical={json.dumps(legacy_ids)};'
                      f'if(fragment&&!current.includes(fragment)&&historical.includes(fragment))location.replace({json.dumps(legacy)}+location.search+location.hash);')
            body = target_page.read_text()
            target_page.write_text(body.replace('</body>', f'<script>{{{script}}}</script></body>', 1))
            reused.append({'url': row['old'], 'legacy': legacy, 'legacy_anchor_count': len(legacy_ids), 'current_anchor_count': len(target_ids)})
            continue
        alias = page_for(site, row['old'])
        if alias.exists():
            raise ValueError('refusing to overwrite current page: ' + row['old'])
        alias.parent.mkdir(parents=True, exist_ok=True)
        # A fragment no longer present in the replacement can still identify an
        # exact section of the historical page. No user string enters HTML.
        script = ('let fragment;try{fragment=decodeURIComponent(location.hash.slice(1));}'
                  'catch{fragment=location.hash.slice(1);}'
                  f'const current={json.dumps(target_ids)};const historical={json.dumps(legacy_ids)};'
                  f'const destination=fragment&&!current.includes(fragment)&&historical.includes(fragment)?{json.dumps(legacy)}:{json.dumps(row["new"])};'
                  'location.replace(destination+location.search+location.hash);')
        target = html.escape(row['new'], quote=True)
        language = row['locale']
        content = (f'<!doctype html><html lang="{language}"><head><meta charset="utf-8">'
                   '<meta name="viewport" content="width=device-width, initial-scale=1">'
                   '<meta name="robots" content="noindex, follow">'
                   f'<link rel="canonical" href="https://docs.olavai.com{target}">'
                   f'<script>{script}</script><noscript><meta http-equiv="refresh" content="0;url={target}"></noscript>'
                   f'<title>Documentation moved</title></head><body><p><a href="{target}">'
                   'Open the current documentation / 查看新版文档</a></p></body></html>')
        alias.write_text(content)
        if set(legacy_ids) <= set(target_ids):
            permanent[row['old']] = row['new']
        aliases.append({'old': row['old'], 'new': row['new'], 'legacy': legacy,
                        'legacy_anchor_count': len(legacy_ids), 'current_anchor_count': len(target_ids)})
    redirects_path = site / '_redirects'
    rules = redirects_path.read_text().rstrip() + '\n' if redirects_path.exists() else ''
    for source, destination in sorted(permanent.items()):
        rules += f'{source} {destination} 301\n{source.rstrip("/")} {destination} 301\n'
    redirects_path.write_text(rules)
    (site / 'robots.txt').write_text(
        'User-agent: *\nAllow: /\n\nSitemap: https://docs.olavai.com/sitemap.xml\n'
    )
    removed_search = 0
    for index in site.rglob('search_index.json'):
        data = json.loads(index.read_text())
        before = len(data['docs'])
        data['docs'] = [entry for entry in data['docs'] if 'legacy' not in entry['location'].strip('/').split('/')]
        removed_search += before - len(data['docs'])
        index.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    for directory in [site / 'legacy', site / 'zh/legacy']:
        for path in directory.rglob('*.html'):
            body = path.read_text()
            body = body.replace('</head>', '<meta name="robots" content="noindex, follow"></head>', 1)
            path.write_text(body)
    sitemap = site / 'sitemap.xml'
    tree = ET.parse(sitemap)
    namespace = 'http://www.sitemaps.org/schemas/sitemap/0.9'
    removed_sitemap = 0
    for entry in list(tree.getroot()):
        location = entry.find(f'{{{namespace}}}loc')
        if location is not None and 'legacy' in urlsplit(location.text).path.strip('/').split('/'):
            tree.getroot().remove(entry)
            removed_sitemap += 1
    ET.register_namespace('', namespace)
    ET.register_namespace('xhtml', 'http://www.w3.org/1999/xhtml')
    rendered_sitemap = ET.tostring(tree.getroot(), encoding='utf-8', xml_declaration=True)
    sitemap.write_bytes(rendered_sitemap)
    if (site / 'sitemap.xml.gz').exists():
        (site / 'sitemap.xml.gz').write_bytes(gzip.compress(rendered_sitemap, mtime=0))
    report = {'schema': 'olav.docs-build/v1', 'legacy_source_files': len(provenance['files']),
              'old_routes': len(mapping), 'aliases': aliases,
              'reused_current_routes': reused,
              'legacy_search_entries_removed': removed_search,
              'legacy_sitemap_urls_removed': removed_sitemap,
              'http_permanent_redirects': permanent,
              'redirect_transport': 'HTTP 301 for anchor-compatible aliases, with HTML compatibility pages for archived anchors'}
    (site / 'docs-build-manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'site_dir': str(site), 'legacy_source_files': len(provenance['files']), 'compatibility_aliases': len(aliases)}))


if __name__ == '__main__':
    main()
