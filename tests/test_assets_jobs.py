from __future__ import annotations

import json
import struct

import pytest

from stacks.assets.intake import (
    CONVERSION_HINT,
    PhiRefused,
    check_capacity,
    check_raw_capture,
    enforce_phi_refusal,
    phi_screen,
    record_projection,
)
from stacks.assets.store import AssetError, AssetStore
from stacks.core import db as dbmod
from stacks.core.config import MountUnverified, init_config_root
from stacks.jobs import queue as jq


@pytest.fixture()
def env(tmp_path):
    cfg = init_config_root(
        tmp_path / "cfg", tmp_path / "db.sqlite", tmp_path / "nas",
        mount_id="vol-test", write_marker=True,
    )
    conn = dbmod.open_database(cfg.db_path, check_local=False)
    yield conn, cfg
    conn.close()


class TestAssetStore:
    def test_put_and_dedup(self, env, tmp_path):
        conn, cfg = env
        store = AssetStore(conn, cfg)
        f = tmp_path / "a.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        a1, created1 = store.put_file(f, "master", source_urls=["http://x/1"])
        assert created1 and a1.role == "master"
        assert store.abs_path(a1).exists()
        # same bytes, different name: one asset, source urls merged
        g = tmp_path / "b.pdf"
        g.write_bytes(b"%PDF-1.4 fake")
        a2, created2 = store.put_file(g, "master", source_urls=["http://y/2"])
        assert not created2 and a2.id == a1.id
        urls = json.loads(
            conn.execute("SELECT source_urls FROM file_assets WHERE id=?", (a1.id,)).fetchone()[0]
        )
        assert urls == ["http://x/1", "http://y/2"]

    def test_path_is_hash_addressed_no_meaning(self, env, tmp_path):
        conn, cfg = env
        store = AssetStore(conn, cfg)
        f = tmp_path / "Galaga_Schematics_Rev_B.pdf"
        f.write_bytes(b"content-1")
        a, _ = store.put_file(f, "archive" if False else "master")
        assert "Galaga" not in a.rel_path
        assert a.rel_path.startswith(f"master/{a.sha256[:2]}/")

    def test_unverified_mount_blocks_write(self, env, tmp_path):
        conn, cfg = env
        store = AssetStore(conn, cfg)
        (cfg.files_root / ".stacks-volume").unlink()  # simulate dropped mount
        f = tmp_path / "c.pdf"
        f.write_bytes(b"content-2")
        with pytest.raises(MountUnverified):
            store.put_file(f, "master")

    def test_lineage_chain(self, env, tmp_path):
        conn, cfg = env
        store = AssetStore(conn, cfg)
        raw = tmp_path / "raw.png"
        raw.write_bytes(b"\x89PNG\r\n\x1a\nraw")
        master = tmp_path / "master.pdf"
        master.write_bytes(b"%PDF master")
        a_raw, _ = store.put_file(raw, "raw")
        a_master, _ = store.put_file(
            master, "master", parent_asset_id=a_raw.id,
            producer_tool="ocrmypdf", producer_version="16",
        )
        chain = store.lineage(a_master.id)
        assert [a.id for a in chain] == [a_master.id, a_raw.id]

    def test_existing_dest_hash_mismatch_refused(self, env, tmp_path):
        conn, cfg = env
        store = AssetStore(conn, cfg)
        f = tmp_path / "d.pdf"
        f.write_bytes(b"content-3")
        from stacks.core.ids import sha256_file

        sha = sha256_file(str(f))
        dest = cfg.files_root / "master" / sha[:2] / f"{sha}.pdf"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"DIFFERENT BYTES")
        with pytest.raises(AssetError, match="refusing to overwrite"):
            store.put_file(f, "master")


class TestPhiRefusal:
    def test_strong_indicator_refused_and_logged(self, env):
        conn, _ = env
        with pytest.raises(PhiRefused) as exc:
            enforce_phi_refusal(conn, "/intake/suspect.pdf", "Patient Name: J. Doe, MRN 12345")
        assert "no override" in str(exc.value)
        row = conn.execute("SELECT * FROM phi_refusals").fetchone()
        assert row["path"] == "/intake/suspect.pdf"
        assert row["reason_code"].startswith("phi-")
        assert "J. Doe" not in row["reason_code"]  # never content

    def test_single_weak_term_fails_closed(self):
        refused, reason = phi_screen("the physician reviewed the installation")
        assert refused and reason.startswith("phi-uncertain")

    def test_arcade_manual_passes(self, env):
        conn, _ = env
        enforce_phi_refusal(
            conn, "/intake/manual.pdf",
            "Remove the monitor assembly and check the isolation transformer "
            "before adjusting the power supply voltage on the game board.",
        )
        assert conn.execute("SELECT COUNT(*) FROM phi_refusals").fetchone()[0] == 0

    def test_word_boundaries(self):
        # 'impatient' must not trip the 'patient' indicator
        refused, _ = phi_screen("be impatient with the boot sequence outpatient")
        assert not refused


def _tiff(tmp_path, compression: int, name="t.tif") -> str:
    p = tmp_path / name
    header = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    ifd = struct.pack("<H", 1)
    ifd += struct.pack("<HHI", 259, 3, 1) + struct.pack("<HH", compression, 0)
    ifd += struct.pack("<I", 0)
    p.write_bytes(header + ifd)
    return str(p)


class TestLosslessRaw:
    def test_png_ok(self, tmp_path):
        p = tmp_path / "x.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
        assert check_raw_capture(p).ok

    def test_tiff_lzw_ok(self, tmp_path):
        r = check_raw_capture(_tiff(tmp_path, 5))
        assert r.ok and "LZW" in r.format

    def test_tiff_zip_ok(self, tmp_path):
        assert check_raw_capture(_tiff(tmp_path, 8)).ok

    def test_tiff_uncompressed_refused_with_hint(self, tmp_path):
        r = check_raw_capture(_tiff(tmp_path, 1))
        assert not r.ok and r.hint == CONVERSION_HINT and "lossless-COMPRESSED" in r.reason

    def test_tiff_jpeg_compression_refused(self, tmp_path):
        assert not check_raw_capture(_tiff(tmp_path, 7)).ok

    def test_jpeg_refused(self, tmp_path):
        p = tmp_path / "x.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 16)
        r = check_raw_capture(p)
        assert not r.ok and "lossy" in r.reason


class TestCapacity:
    def test_below_threshold_no_job(self, env):
        conn, cfg = env
        status = check_capacity(conn, cfg, usage=(10, 100))
        assert "add_storage_job" not in status
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0

    def test_threshold_opens_one_planned_task(self, env):
        conn, cfg = env
        s1 = check_capacity(conn, cfg, usage=(85, 100))
        s2 = check_capacity(conn, cfg, usage=(90, 100))
        assert s1["add_storage_job"] == s2["add_storage_job"]  # idempotent
        rows = conn.execute("SELECT * FROM jobs WHERE kind='add-storage'").fetchall()
        assert len(rows) == 1 and rows[0]["status"] == "queued"

    def test_projection_recorded(self, env):
        conn, _ = env
        record_projection(conn, "session-1", {"raw": 1000, "master": 2000, "access": 300})
        row = conn.execute("SELECT * FROM storage_projections").fetchone()
        assert json.loads(row["bytes_by_class"])["master"] == 2000


class TestJobs:
    def test_claim_is_exclusive(self, env):
        conn, _ = env
        jid = jq.enqueue(conn, "analyze", {"copy": "cp_1"})
        j1 = jq.claim(conn, "worker-a")
        j2 = jq.claim(conn, "worker-b")
        assert j1["id"] == jid and j2 is None

    def test_expired_lease_reclaimable_and_stale_worker_rejected(self, env):
        conn, _ = env
        jq.enqueue(conn, "analyze")
        j1 = jq.claim(conn, "worker-a", lease_seconds=-1)  # lease already expired
        j2 = jq.claim(conn, "worker-b")
        assert j2 is not None and j2["id"] == j1["id"]
        assert not jq.heartbeat(conn, j1["id"], "worker-a")  # stale worker rejected
        assert not jq.complete(conn, j1["id"], "worker-a")
        assert jq.complete(conn, j2["id"], "worker-b", {"ok": True})

    def test_heartbeat_extends(self, env):
        conn, _ = env
        jq.enqueue(conn, "fixity")
        j = jq.claim(conn, "w1", lease_seconds=60)
        assert jq.heartbeat(conn, j["id"], "w1", lease_seconds=600)

    def test_bounded_retries_then_dead(self, env):
        conn, _ = env
        jq.enqueue(conn, "flaky", max_attempts=2)
        s = []
        for _ in range(2):
            j = jq.claim(conn, "w1")
            s.append(jq.fail(conn, j["id"], "w1", "boom"))
        assert s == ["queued", "dead"]
        assert jq.claim(conn, "w1") is None

    def test_idempotency_key_replay(self, env):
        conn, _ = env
        a = jq.enqueue(conn, "ingest", idempotency_key="ingest:sha:abc")
        b = jq.enqueue(conn, "ingest", idempotency_key="ingest:sha:abc")
        assert a == b

    def test_priority_order(self, env):
        conn, _ = env
        jq.enqueue(conn, "low", priority=0)
        high = jq.enqueue(conn, "high", priority=10)
        assert jq.claim(conn, "w")["id"] == high
