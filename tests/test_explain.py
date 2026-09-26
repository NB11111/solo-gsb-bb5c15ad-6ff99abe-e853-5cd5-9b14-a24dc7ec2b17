"""/api/runners/{id}/splits/explain 入选解释测试。夹具 client 见 conftest.py。"""
from __future__ import annotations


def post(client, event_no, runner, station, occurred_at, corrects=None):
    body = {
        "event_no": event_no,
        "runner_id": runner,
        "station_seq": station,
        "occurred_at": occurred_at,
    }
    if corrects is not None:
        body["corrects"] = corrects
    return client.post("/api/checkins", json=body)


def explain(client, runner):
    return client.get(f"/api/runners/{runner}/splits/explain")


def stations_map(data):
    return {s["station_seq"]: s for s in data["stations"]}


def find_checkin(station, event_no):
    return next(c for c in station["checkins"] if c["event_no"] == event_no)


# ---------- 基础形态 ----------


def test_explain_unknown_runner_404(client):
    assert explain(client, "NOBODY").status_code == 404
    # 与 /splits 的 404 语义一致
    assert client.get("/api/runners/NOBODY/splits").status_code == 404


def test_explain_seed_complete_runner_lists_all_active_selected(client):
    r = explain(client, "R001")
    assert r.status_code == 200
    data = r.json()
    assert data["runner_id"] == "R001"
    assert data["station_count"] == 6
    assert [s["station_seq"] for s in data["stations"]] == [1, 2, 3, 4, 5, 6]

    for seq in range(1, 7):
        st = stations_map(data)[seq]
        assert len(st["checkins"]) == 1
        c = st["checkins"][0]
        assert c["status"] == "active"
        assert c["selected"] is True
        assert c["reason"] is None
        assert st["selected_event_no"] == c["event_no"] == f"S-R001-{seq}"


def test_explain_splits_match_splits_endpoint_and_same_read(client):
    # 解释中的分段与 /splits 完全一致（同一次读取的同一份行快照计算）
    data = explain(client, "R001").json()
    live = client.get("/api/runners/R001/splits").json()
    assert data["splits"] == live

    # 入选事件号与分段 stations 中采用的事件号一一对应
    selected = {s["station_seq"]: s["selected_event_no"] for s in data["stations"]}
    used = {st["station_seq"]: st["event_no"] for st in data["splits"]["stations"]}
    assert selected == used


# ---------- 未入选原因：已更正 / 已撤销 ----------


def test_explain_seed_correction_marks_origin_superseded(client):
    data = explain(client, "R004").json()
    st2 = stations_map(data)[2]
    # 两条打卡都列出：更正后的 08:40 与被取代的 12:30
    assert [c["event_no"] for c in st2["checkins"]] == ["S-R004-2", "S-R004-2X"]
    newer = find_checkin(st2, "S-R004-2")
    older = find_checkin(st2, "S-R004-2X")
    assert newer["status"] == "active" and newer["selected"] is True
    assert newer["reason"] is None
    assert older["status"] == "superseded" and older["selected"] is False
    assert older["reason"] == "已更正"
    assert st2["selected_event_no"] == "S-R004-2"
    # 即使被更正记录时间更晚，分段采用的仍是更正后的事件
    assert data["splits"]["stations"][1]["event_no"] == "S-R004-2"


def test_explain_seed_revoked_checkin_not_selected(client):
    data = explain(client, "R005").json()
    st2 = stations_map(data)[2]
    assert st2["selected_event_no"] is None
    (only,) = st2["checkins"]
    assert only["event_no"] == "S-R005-2"
    assert only["status"] == "revoked"
    assert only["selected"] is False
    assert only["reason"] == "已撤销"


def test_explain_earlier_superseded_does_not_participate(client):
    # 被更正的记录即使时间更早也不参与入选（仅 active 参与）
    post(client, "K-1", "K001", 1, "2026-09-25T08:00:00")
    post(client, "K-2X", "K001", 2, "2026-09-25T08:30:00")  # 时间更早但被更正
    post(client, "K-2", "K001", 2, "2026-09-25T08:40:00", corrects="K-2X")
    st2 = stations_map(explain(client, "K001").json())[2]
    assert [c["event_no"] for c in st2["checkins"]] == ["K-2", "K-2X"]
    assert st2["selected_event_no"] == "K-2"
    assert find_checkin(st2, "K-2X")["reason"] == "已更正"


# ---------- 未入选原因：较晚 / 同时间平局 ----------


def test_explain_later_active_marked_later(client):
    post(client, "L-1", "L001", 1, "2026-09-25T08:00:00")
    post(client, "L-2a", "L001", 2, "2026-09-25T09:00:00")
    post(client, "L-2b", "L001", 2, "2026-09-25T08:40:00")  # 更早，入选
    post(client, "L-2c", "L001", 2, "2026-09-25T09:10:00")  # 更晚
    st2 = stations_map(explain(client, "L001").json())[2]
    # 同站按 UTC 时间升序
    assert [c["event_no"] for c in st2["checkins"]] == ["L-2b", "L-2a", "L-2c"]
    assert st2["selected_event_no"] == "L-2b"
    for ev, reason in (("L-2b", None), ("L-2a", "较晚"), ("L-2c", "较晚")):
        c = find_checkin(st2, ev)
        assert c["selected"] is (reason is None)
        assert c["reason"] == reason


def test_explain_same_time_tie_breaks_by_event_no(client):
    post(client, "T-1", "TIE", 1, "2026-09-25T08:00:00")
    post(client, "T-2b", "TIE", 2, "2026-09-25T08:30:00")
    post(client, "T-2a", "TIE", 2, "2026-09-25T08:30:00")  # 同时间，事件号更小 -> 入选
    st2 = stations_map(explain(client, "TIE").json())[2]
    # 同时间按事件号升序
    assert [c["event_no"] for c in st2["checkins"]] == ["T-2a", "T-2b"]
    assert st2["selected_event_no"] == "T-2a"
    assert find_checkin(st2, "T-2a")["selected"] is True
    loser = find_checkin(st2, "T-2b")
    assert loser["selected"] is False
    assert loser["reason"] == "同时间平局"
    # 分段采用的也是平局胜出者
    used = {s["station_seq"]: s["event_no"] for s in explain(client, "TIE").json()["splits"]["stations"]}
    assert used[2] == "T-2a"


def test_explain_utc_timezone_normalized_ordering(client):
    # 16:00+08:00 == 08:00Z；与 08:00Z 平局，按事件号升序
    post(client, "Z-2b", "TZ0", 2, "2026-09-25T16:00:00+08:00")
    post(client, "Z-2a", "TZ0", 2, "2026-09-25T08:00:00+00:00")
    st2 = stations_map(explain(client, "TZ0").json())[2]
    assert [c["event_no"] for c in st2["checkins"]] == ["Z-2a", "Z-2b"]
    assert st2["selected_event_no"] == "Z-2a"
    assert find_checkin(st2, "Z-2b")["reason"] == "同时间平局"


# ---------- 更正 / 撤销 / 补传后即时更新 ----------


def test_explain_updates_after_backfill_revoke_and_correction(client):
    post(client, "U-1", "U001", 1, "2026-09-25T08:00:00")
    post(client, "U-3", "U001", 3, "2026-09-25T09:00:00")

    # 补传第 2 站前：解释中没有站点 2，分段 1→2、2→3 待定
    data = explain(client, "U001").json()
    assert set(stations_map(data)) == {1, 3}
    segs = {(s["from_station"], s["to_station"]): s for s in data["splits"]["segments"]}
    assert segs[(1, 2)]["status"] == "pending"
    assert segs[(2, 3)]["status"] == "pending"

    # 乱序补传后即时出现站点 2 并入选，分段恢复
    post(client, "U-2", "U001", 2, "2026-09-25T08:30:00")
    data = explain(client, "U001").json()
    st2 = stations_map(data)[2]
    assert st2["selected_event_no"] == "U-2"
    segs = {(s["from_station"], s["to_station"]): s for s in data["splits"]["segments"]}
    assert segs[(1, 2)]["status"] == "ok" and segs[(1, 2)]["duration_seconds"] == 1800
    assert segs[(2, 3)]["status"] == "ok"

    # 撤销入选事件后：站点 2 无入选，记录标已撤销，分段重新待定
    assert client.post("/api/checkins/U-2/revoke").status_code == 200
    data = explain(client, "U001").json()
    st2 = stations_map(data)[2]
    assert st2["selected_event_no"] is None
    assert find_checkin(st2, "U-2")["reason"] == "已撤销"
    segs = {(s["from_station"], s["to_station"]): s for s in data["splits"]["segments"]}
    assert segs[(1, 2)]["status"] == "pending"

    # 重新补传一条更晚的，再用更正替换为更早时间：解释即时反映
    post(client, "U-2L", "U001", 2, "2026-09-25T08:55:00")
    post(client, "U-2F", "U001", 2, "2026-09-25T08:35:00", corrects="U-2L")
    data = explain(client, "U001").json()
    st2 = stations_map(data)[2]
    assert [c["event_no"] for c in st2["checkins"]] == ["U-2F", "U-2L", "U-2"]
    assert st2["selected_event_no"] == "U-2F"
    assert find_checkin(st2, "U-2L")["reason"] == "已更正"
    assert find_checkin(st2, "U-2")["reason"] == "已撤销"
    segs = {(s["from_station"], s["to_station"]): s for s in data["splits"]["segments"]}
    assert segs[(1, 2)]["status"] == "ok" and segs[(1, 2)]["duration_seconds"] == 35 * 60
    assert segs[(2, 3)]["status"] == "ok" and segs[(2, 3)]["duration_seconds"] == 25 * 60


def test_explain_revoked_previously_winner_allows_next_active(client):
    # 撤销当前入选者后，同站次早的有效打卡接管
    post(client, "R-2a", "R00X", 2, "2026-09-25T08:30:00")
    post(client, "R-2b", "R00X", 2, "2026-09-25T08:40:00")
    st2 = stations_map(explain(client, "R00X").json())[2]
    assert st2["selected_event_no"] == "R-2a"
    client.post("/api/checkins/R-2a/revoke")
    st2 = stations_map(explain(client, "R00X").json())[2]
    assert st2["selected_event_no"] == "R-2b"
    assert find_checkin(st2, "R-2a")["reason"] == "已撤销"
    assert find_checkin(st2, "R-2b")["selected"] is True
    assert find_checkin(st2, "R-2b")["reason"] is None
