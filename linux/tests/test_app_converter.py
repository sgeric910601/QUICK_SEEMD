"""萬用轉換頁的資料模型（不需要畫面）：加入、分析、選取範圍、格式選擇、選項區塊、轉換流程。

轉換引擎是另一個工作的範圍：這裡用替身取代 converter.targets_for／unavailable。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quickseemd.app import converter_model as cmod
from quickseemd.core import converter
from quickseemd.core.converter import Target, Unavailable
from quickseemd.core.converter.formats import BY_ID


def T(fid: str, backend: str | None = None) -> Target:  # noqa: N802
    return Target(BY_ID[fid], "image", None, backend)


def _analysis(fids, **flags) -> cmod.Analysis:
    return cmod.Analysis(
        targets=tuple(T(f) for f in fids),
        unavailable=(),
        kind=flags.pop("kind", "測試"),
        detail=flags.pop("detail", None),
        **flags,
    )


@pytest.fixture
def paths(tmp_path: Path) -> list[Path]:
    out = []
    for name in ("a.png", "b.png", "c.mp4", "d.pdf", "e.txt"):
        p = tmp_path / name
        p.write_bytes(b"x" * 10)
        out.append(p)
    return out


def _model_with(paths, analyses) -> cmod.ConverterModel:
    m = cmod.ConverterModel(settings_loader=lambda: {})
    jobs = m.add(paths)
    for job, a in zip(jobs, analyses):
        m.apply_analysis(job.uid, a)
    return m


def test_add_skips_duplicates_and_missing(paths, tmp_path):
    m = cmod.ConverterModel(settings_loader=lambda: {})
    first = m.add(paths[:2])
    assert len(first) == 2 and m.selection == {j.uid for j in first}
    again = m.add([paths[0], tmp_path / "missing.png", paths[2]])
    assert [j.path for j in again] == [paths[2]]
    assert m.jobs[0].size_text == "10 位元組" and m.jobs[0].analyzing


def test_folder_job_text(tmp_path):
    d = tmp_path / "資料夾"
    d.mkdir()
    (d / "x").write_text("1")
    (d / "y").write_text("2")
    m = cmod.ConverterModel(settings_loader=lambda: {})
    job = m.add([d])[0]
    assert job.size_text == "" and job.detail == "2 個項目"


def test_scope_and_common_targets(paths):
    m = _model_with(
        paths[:3],
        [
            _analysis(["jpeg", "webp", "pdf"], is_image=True),
            _analysis(["jpeg", "pdf"], is_image=True),
            _analysis(["mp4", "anim-gif"], is_video=True),
        ],
    )
    m.select_all()
    assert m.scope_description() == "全部 3 個檔案"
    assert m.common_targets() == []
    assert m.scope_is_mixed()
    a, b, c = (j.uid for j in m.jobs)
    m.select(a, extend=False)
    assert m.scope_description() == "「a.png」"
    m.select(b, extend=True)
    assert m.scope_description() == "選取的 2 個檔案"
    assert [t.id for t in m.common_targets()] == ["jpeg", "pdf"]
    groups = m.grouped_targets()
    assert [(g.id, [t.id for t in ts]) for g, ts in groups] == [
        ("image", ["jpeg"]),
        ("document", ["pdf"]),
    ]
    m.select(b, extend=True)
    assert m.scope_description() == "「a.png」"
    m.selection = frozenset()
    assert m.scope_description() == "全部 3 個檔案"


def test_choose_toggles_and_partial(paths):
    m = _model_with(paths[:2], [_analysis(["jpeg", "png"]), _analysis(["jpeg", "png"])])
    a, b = (j.uid for j in m.jobs)
    m.select(a, extend=False)
    m.choose("jpeg")
    m.select_all()
    assert m.selection_state("jpeg") == (False, True)
    m.choose("jpeg")
    assert m.selection_state("jpeg") == (True, True)
    m.choose("jpeg")
    assert m.selection_state("jpeg") == (False, False)
    assert m.ready_jobs() == []


def test_last_choice_applies_to_new_jobs(paths):
    m = _model_with(paths[:1], [_analysis(["jpeg", "png"])])
    m.choose("png")
    job = m.add([paths[1]])[0]
    m.apply_analysis(job.uid, _analysis(["png", "webp"]))
    assert m.job(job.uid).target_id == "png"
    m.apply_analysis(job.uid, _analysis(["webp"]))
    assert m.job(job.uid).target_id is None


def test_relevant_sections(paths):
    m = _model_with(
        paths,
        [
            _analysis(["jpeg", "ico"], is_image=True),
            _analysis(["webp", "pdf"], is_image=True),
            _analysis(["anim-gif", "jpeg", "mp4", "m4a"], is_video=True),
            _analysis(["png", "txt"], is_pdf=True),
            _analysis(["utf8", "json"], is_textual=True, detail="UTF-8 · LF"),
        ],
    )
    a, b, c, d, e = (j.uid for j in m.jobs)

    def only(uid, fid):
        m.select(uid, extend=False)
        m.choose(fid)
        return m.relevant_sections()

    assert only(a, "jpeg") == ["quality", "size"]
    assert only(a, "ico") == []
    assert only(b, "pdf") == ["size"]
    assert only(c, "anim-gif") == ["gif"]
    assert only(c, "jpeg") == ["quality", "size", "frame"]
    assert only(c, "mp4") == ["video_size"]
    assert only(c, "m4a") == ["bitrate"]
    assert only(d, "png") == ["size", "pdf_dpi"]
    assert only(e, "utf8") == ["encoding"]
    assert m.detected_encoding() == "UTF-8"


def test_convert_flow_and_banner(paths):
    m = _model_with(paths[:3], [_analysis(["png"]), _analysis(["png"]), _analysis(["png"])])
    m.select_all()
    m.choose("png")
    assert m.convert_title() == "轉換 3 個檔案"
    ids = m.begin_convert()
    assert len(ids) == 3 and m.running
    assert all(m.job(i).status.kind == "queued" for i in ids)
    uid = m.next_queued()
    m.mark_running(uid)
    m.set_progress(uid, 0.004)
    assert m.job(uid).status.fraction == 0
    m.set_progress(uid, 0.5)
    assert m.overall_progress() == pytest.approx(0.5 / 3)
    m.finish(uid, {"outputs": [str(paths[0]) + ".png"]})
    assert m.job(uid).status.kind == "done"
    uid2 = m.next_queued()
    m.mark_running(uid2)
    m.finish(uid2, {"error": "壞掉了"})
    assert m.job(uid2).status.error == "壞掉了"
    uid3 = m.next_queued()
    m.mark_running(uid3)
    m.finish(uid3, {"outputs": ["/x/y.png"]})
    assert m.next_queued() is None
    m.end_convert()
    assert not m.running
    assert m.banner.message == "完成 2 個，1 個失敗。" and m.banner.is_error
    assert m.finished_count() == 2
    m.clear_finished()
    assert len(m.jobs) == 1


def test_convert_cancel_resets_queue(paths):
    m = _model_with(paths[:3], [_analysis(["png"])] * 3)
    m.select_all()
    m.choose("png")
    m.begin_convert()
    uid = m.next_queued()
    m.mark_running(uid)
    m.cancel_run()
    m.finish(uid, {"error": "已取消。", "cancelled": True})
    assert m.job(uid).status.kind == "cancelled"
    assert m.next_queued() is None
    m.end_convert()
    assert [j.status.kind for j in m.jobs] == ["cancelled", "idle", "idle"]
    assert m.banner is None
    assert m.convert_title() == "轉換 3 個檔案"


def test_all_success_banner_and_single(paths):
    m = _model_with(paths[:2], [_analysis(["png"])] * 2)
    m.select_all()
    m.choose("png")
    m.begin_convert()
    for _ in range(2):
        u = m.next_queued()
        m.mark_running(u)
        m.finish(u, {"outputs": ["/o/x.png"]})
    m.end_convert()
    assert m.banner.message == "全部完成，共 2 個檔案。" and not m.banner.is_error
    m2 = _model_with(paths[:1], [_analysis(["png"])])
    m2.choose("png")
    assert m2.convert_title() == "轉換"
    m2.begin_convert()
    u = m2.next_queued()
    m2.finish(u, {"outputs": ["/o/x.png"]})
    m2.end_convert()
    assert m2.banner is None


def test_remove_and_merge(paths):
    m = _model_with(
        paths, [_analysis(["png"], is_image=True)] * 2 + [_analysis(["mp4"], is_video=True)] * 3
    )
    assert [j.path.name for j in m.mergeable_jobs()] == ["a.png", "b.png"]
    m.merge_finished({"outputs": ["/o/合併.pdf"]}, 2)
    assert m.banner.message == "已將 2 個檔案合併成「合併.pdf」"
    m.merge_finished({"error": "沒辦法"}, 2)
    assert m.banner.message == "合併失敗：沒辦法" and m.banner.is_error
    first = m.jobs[0].uid
    m.select(first, extend=False)
    m.remove_selected()
    assert len(m.jobs) == 4 and not m.selection
    m.remove_all()
    assert m.jobs == ()


def test_output_mode_and_folder(tmp_path, paths):
    saved: list[dict] = []
    m = cmod.ConverterModel(
        settings_loader=lambda: {
            "converter.output_mode": "folder",
            "converter.output_folder": str(tmp_path),
        },
        settings_saver=saved.append,
    )
    assert m.output_mode == "folder" and m.output_folder == tmp_path
    assert m.output_folder_spec() == str(tmp_path)
    m.set_output_mode("same")
    assert m.output_folder_spec() is None
    assert saved[-1] == {"converter.output_mode": "same"}
    m.set_output_folder(tmp_path / "x")
    assert saved[-1]["converter.output_mode"] == "folder"
    m2 = cmod.ConverterModel(
        settings_loader=lambda: {"converter.output_mode": "folder", "converter.output_folder": ""}
    )
    assert m2.output_folder is None and m2.needs_folder()


def test_analyze_path_with_fake_engine(monkeypatch, paths):
    monkeypatch.setattr(converter, "targets_for", lambda p: [T("jpeg")])
    monkeypatch.setattr(converter, "unavailable", lambda p: [Unavailable("HEIC", "需要安裝", None)])
    monkeypatch.setattr(converter, "describe", lambda p: ("純文字", "UTF-8 · LF"))
    a = cmod.analyze_path(paths[0])
    assert [t.id for t in a.targets] == ["jpeg"] and a.unavailable[0].formats == "HEIC"
    assert not a.engine_missing
    assert (a.kind, a.detail) == ("純文字", "UTF-8 · LF")


def test_analyze_path_describe_failure_keeps_mime_kind(monkeypatch, paths):
    def broken(p):  # noqa: ANN001, ANN202
        raise RuntimeError("bad")

    monkeypatch.setattr(converter, "targets_for", lambda p: [T("jpeg")])
    monkeypatch.setattr(converter, "unavailable", lambda p: [])
    monkeypatch.setattr(converter, "describe", broken)
    a = cmod.analyze_path(paths[0])
    assert a.kind and a.detail is None and [t.id for t in a.targets] == ["jpeg"]


def test_analyze_path_real_engine(tmp_path):
    csv = tmp_path / "表.csv"
    csv.write_text("名稱,數量\n蘋果,3\n", "utf-8")
    a = cmod.analyze_path(csv)
    assert not a.engine_missing and a.is_textual
    assert a.detail and a.detail.startswith("UTF-8")
    assert "json" in {t.id for t in a.targets}


def test_detected_encoding_from_detail(paths):
    m = _model_with(
        paths[:3],
        [
            _analysis(["utf8"], is_textual=True, detail="UTF-8 · LF"),
            _analysis(["utf8"], is_textual=True, detail="UTF-8 · CRLF"),
            _analysis(["utf8"], is_textual=True, detail="Big5 · LF"),
        ],
    )
    a, b, c = (j.uid for j in m.jobs)
    m.select(a, extend=False)
    m.select(b, extend=True)
    assert m.detected_encoding() == "UTF-8"
    m.select(c, extend=True)
    assert m.detected_encoding() == "多種編碼"
    assert cmod.encoding_of("UTF-8（含 BOM） · CRLF") == "UTF-8（含 BOM）"
    assert cmod.encoding_of("Big5") == "Big5"


def test_analyze_path_engine_missing(monkeypatch, paths):
    def missing(p):  # noqa: ANN001, ANN202
        raise NotImplementedError("萬用轉換引擎尚未實作")

    monkeypatch.setattr(converter, "targets_for", missing)
    a = cmod.analyze_path(paths[4])
    assert a.engine_missing and a.targets == () and a.is_textual


def test_analyze_path_engine_error(monkeypatch, paths):
    def broken(p):  # noqa: ANN001, ANN202
        raise RuntimeError("bad")

    monkeypatch.setattr(converter, "targets_for", broken)
    a = cmod.analyze_path(paths[3])
    assert a.targets == () and not a.engine_missing and a.is_pdf


def test_unavailable_hints_dedup_and_engine_flag(paths):
    m = cmod.ConverterModel(settings_loader=lambda: {})
    jobs = m.add(paths[:2])
    u = Unavailable("MP3", "需要 ffmpeg", "ffmpeg")
    for j in jobs:
        m.apply_analysis(j.uid, cmod.Analysis((), (u,), "x", None))
    assert m.unavailable_hints() == [u]
    m.apply_analysis(jobs[0].uid, cmod.Analysis((), (), "x", None, engine_missing=True))
    assert m.engine_missing


def test_option_tables():
    assert cmod.SECTION_TITLES["quality"][0] == "品質"
    assert cmod.ENCODINGS[0] == ("auto", "自動偵測")
    assert cmod.VIDEO_SIZES[-1] == (2160, "4K")
    assert cmod.quality_text(0.85) == "85%"
    assert cmod.format_tooltip(T("mp3", "ffmpeg")) == "轉換成 MP3（使用 FFmpeg）"
    assert cmod.format_tooltip(T("png")) == "轉換成 PNG"
    assert cmod.status_text(cmod.JobStatus("running", 0.0)) == "轉換中…"
    assert cmod.status_text(cmod.JobStatus("running", 0.42)) == "42%"
    assert cmod.status_text(cmod.JobStatus("done", outputs=(Path("/a/b.png"),))) == "完成：b.png"
    assert cmod.status_text(cmod.JobStatus("done", outputs=(Path("/a"), Path("/b")))) == (
        "完成：2 個項目"
    )
    assert cmod.status_text(cmod.JobStatus("queued")) == "等待中…"
    assert cmod.status_text(cmod.JobStatus("cancelled")) == "已取消"
    assert cmod.status_text(cmod.JobStatus("failed", error="x")) == "x"
    assert cmod.status_text(cmod.JobStatus("idle")) == ""
