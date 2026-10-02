"""HTML parser fixtures (SPEC 11.1a): parsers develop offline against
representative fixture markup; real crawls only on the mini (phase 5)."""

from __future__ import annotations

from stacks.acquisition.parsers import parse_open_directory, parse_tama_table

TAMA_HTML = """
<html><body>
<table class="listing">
  <tr><th>Title</th><th>Category</th><th>Size</th></tr>
  <tr>
    <td><a href="/manuals/G/Galaga.pdf">Galaga Service Manual</a></td>
    <td>Video Game Manuals</td><td>4.2 MB</td>
  </tr>
  <tr>
    <td><a href="/manuals/D/DigDug_Schematics.pdf">Dig Dug Schematic Package</a></td>
    <td>Schematics</td><td>12 MB</td>
  </tr>
  <tr><td colspan="3">— advertisement —</td></tr>
</table>
<table><tr><td><a href="?page=2">Next</a></td></tr></table>
</body></html>
"""

APACHE_INDEX = """
<html><head><title>Index of /manuals/ARCADE</title></head><body>
<h1>Index of /manuals/ARCADE</h1><pre>
<a href="?C=N;O=D">Name</a> <a href="?C=M;O=A">Last modified</a>
<hr>
<a href="../">Parent Directory</a>
<a href="atari/">atari/</a>                2019-05-01  -
<a href="Defender%20Manual.pdf">Defender Manual.pdf</a>   2018-03-11  2.1M
<a href="galaga_schem.pdf">galaga_schem.pdf</a>     2017-01-09  8.0M
</pre><hr></body></html>
"""


class TestTamaTable:
    def test_rows_with_links(self):
        rows = parse_tama_table(TAMA_HTML, base_url="https://www.arcade-museum.com/")
        titles = [r.title for r in rows if r.href.endswith(".pdf")]
        assert titles == ["Galaga Service Manual", "Dig Dug Schematic Package"]

    def test_urls_absolute(self):
        rows = parse_tama_table(TAMA_HTML, base_url="https://www.arcade-museum.com/")
        assert rows[0].href == "https://www.arcade-museum.com/manuals/G/Galaga.pdf"

    def test_extra_cells_preserved(self):
        rows = parse_tama_table(TAMA_HTML)
        assert "Video Game Manuals" in rows[0].extra

    def test_decoration_rows_skipped(self):
        rows = parse_tama_table(TAMA_HTML)
        assert all("advertisement" not in r.title for r in rows)


class TestOpenDirectory:
    def test_entries(self):
        entries = parse_open_directory(APACHE_INDEX, base_url="http://pdf.textfiles.com/manuals/ARCADE/")
        names = {e.name for e in entries}
        assert names == {"atari", "Defender Manual.pdf", "galaga_schem.pdf"}

    def test_dirs_vs_files(self):
        entries = parse_open_directory(APACHE_INDEX)
        by_name = {e.name: e for e in entries}
        assert by_name["atari"].is_dir
        assert not by_name["galaga_schem.pdf"].is_dir

    def test_parent_and_sort_links_excluded(self):
        entries = parse_open_directory(APACHE_INDEX)
        assert all(not e.href.startswith("?") for e in entries)
        assert all(e.name not in ("..", "") for e in entries)

    def test_percent_decoding(self):
        entries = parse_open_directory(APACHE_INDEX)
        assert "Defender Manual.pdf" in {e.name for e in entries}
