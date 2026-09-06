# -*- coding: utf-8 -*-
"""放送用のM3Uプレイリストを書く（2026-09-05）。

★なぜM3Uなのか（`infra/RADIODJ_SIYOU.md` に調査の全文）
  RadioDJには **Load M3U Playlist by Date Mask** というイベント動作がある。
  日付マスク（.NETの書式）に一致する .m3u を指定フォルダから読み込む。
  これを使うと**順番も曲も、こちら（生成側）が完全に決められる**。
  - こずえが「それでは聴いてください——『南の星』」と読み上げた曲を、
    M3Uにそのパスで書けば**必ずその曲が流れる**
  - トーク→曲→ジングル→トークの並びも**M3Uの行順で決まる**
  → 「7本のブロックをRadioDJでどう並べるか」という問題が消える。

★★事故の時の挙動（nordw方針「番組が流れず、しれっと曲が流れ続けるのがベター」）
  RadioDJのイベントで **Open Positions を `Top`（先頭に差し込む）** にすること。
  - M3Uが読めた → 番組が先頭に入って流れる
  - M3Uが無い   → **何も差し込まれず、Auto DJが積んだ曲がそのまま流れ続ける**
  ⚠ `Replace`（キューを全部入れ替える）を選ぶと、失敗時に無音になるリスクがある。
  ★CEBDR24は既に24時間曲が流れている＝**Auto DJはもう動いている**。
    「曲が流れ続ける」は新しく作るものではなく、**既にある基底状態**。

★放送は6時・7時・8時の3回。**回ごとにM3Uを分ける**。
  マスクは `yyyy-MM-dd.HH` → `2026-09-05.06.m3u` / `...07.m3u` / `...08.m3u`
  パスA が3本まとめて書き、パスB が各回のおたよりを差し替えたら**その回だけ書き直す**。

★★★2026-09-06 設計変更: **M3Uを前半・後半の2本に割る。**
  nordw「**出勤や登校したあとによまれたってうれしくないの！！**」

  ⚠ 直前まで「おたよりは放送の**直前**に締め切る」設計にしていた。それだと
    6時の回に書いた人が読まれるのは7時の回。**その頃には家を出ている。**
    `chat_collect.py` の冒頭に書いてある存在理由（「いま書いたら今すぐ読まれる」）を
    送出の都合で殺していた。**番組の目的のほうが上。**

  では放送中に差し替えられるか——**1本のM3Uでは無理**。
  RadioDJは放送開始時にM3Uを読み込む。6:06に書き直しても**読み直されない**。
  → **前半(playlists/)と後半(playlists_b/)に割り、イベントを2つ置く。**

      6:00:00  イベント1  playlists/  を読む     … パスAが作った前半（確定済み）
      6:08:00  イベント2  playlists_b/ を読む    … パスBが放送中に差し替えた後半

  ★事故に強い形になっている:
    - 後半M3UはパスAが**フォールバック版おたよりで先に書いておく**。
      パスBが間に合わなければ**そのまま流れる**（無音にならない）
    - 後半が欠けても**前半は放送される**（1本だった頃は全滅していた）

  ★★★イベントの中身（2026-09-06 確定。プロハンさんの実機回答とv2.0.4.7のスクショで）:
    ⚠ 単純な Bottom では成立しない。**Auto DJ が常に8曲をキューに保持している**
      （プロハンさん実測「現在流れている曲を除き、8曲が常にキューに入っています」）。
      後半を Bottom で足すと [前半の残り, AutoDJの8曲(約30分), 後半] になり、
      おたよりが6:40頃に流れる。→ **番組の間だけ Auto DJ を止める**。
    前半イベント(6:00:00)のアクション（この順）:
      1 Disable AutoDJ / 2 Clear Playlist / 3 Load M3U By Date Mask(playlists, Top)
      / 4 Start Playing
    後半イベント(6:08:00)のアクション:
      1 Load M3U By Date Mask(playlists_b, Bottom) / 2 Enable AutoDJ
      ★Enable が後半の下に8曲を補充する＝エンディング後、自然に曲へ戻る。
      ★後半M3Uが無い日も Enable は走る＝6:08以降の無音を必ず塞ぐ栓になっている。
    ⚠ この形の代償: 番組が丸ごと無い日は、6:00の Clear+Disable で
      **再生中の曲が終わってから6:08まで無音**になりうる。
      → VPSの見張り(5:30)が「番組が無い」と気づいたら**緊急M3U**（曲フォルダから
        機械的に選んだ4曲）を書いて塞ぐ（`kozue_bangumi.ps1` Watch）。

⚠ 未確認（`infra/RADIODJ_SIYOU.md` 参照）:
  - M3Uに書くパスの形式（絶対パスで書いている。VPS上で通るかは実機確認）
  - ★**VPS上の曲フォルダのパス**。下の SONG_DIR は曲リストのCSVのPath列から取ったもので、
    **VPS上も同じ場所かはプロハンさんに未確認**
  - M3U経由でも再生ログ・権利処理が正しく残るか
"""
import datetime
# ★PureWindowsPath を使う理由は下の vps() を見ること
from pathlib import Path, PureWindowsPath

# ★VPS上の置き場所。**ここはプロハンさんに確認が要る。**
#   曲リスト(cyberenkaplaylist0904.csv)の Path 列が C:\CYBER_ENKA_STREAM\tracks_norm\ だった。
SONG_DIR = r"C:\CYBER_ENKA_STREAM\tracks_norm"
# こずえの音声とジングルの置き場所（rcloneが降ろす先。VPS_SETUP.md と合わせる）
VOICE_ROOT = r"C:\kozue_asa"

BROADCAST_HOURS = (6, 7, 8)     # 2026-09-05 確定。めざましテレビ方式で同じ回を3回

# 放送順。★make_through.py と同じ並びにすること（片方だけ変えると食い違う）
#   ("seg", ブロック名) … こずえの音声
#   ("song1"/"song2")   … その日の曲
#   ("jingle")          … 曲明けのジングル（★曲の前には入れない。2026-09-04 プロハン指示）
#
# ★★2026-09-06 おたよりを**曲2のあと**へ移した。理由は締切:
#   実測の尺で並べると、元の位置（ニュースの直後）だとおたよりの再生開始が **6:08:15**。
#   パスBは「準備3分 + チャット収集3分 + 生成3分 + 納品と同期1分半」で、
#   VPSに届くのが **6:07頃**。margin 1分。**曲2のあとに動かすと 6:11:17 になり 3分増える。**
#   ⚠ 台本の文言も直すこと（generate_v2.py の「ニュースのあとで読む」→「二曲目のあとで読む」）
#
# ★("split",) から後ろが「後半」＝放送中に差し替わる部分。ここより前は放送開始時に確定。
ORDER = [
    ("seg", "talk1"),
    ("song1",), ("jingle",),
    ("seg", "traffic"),
    ("seg", "sa"),
    ("seg", "news"),
    ("song2",), ("jingle",),
    ("split",),                  # ←★ここで前半・後半が分かれる
    ("mail",),                   # パスBの本番版があればそれ、無ければ mail_fb
    ("seg", "uranai"),
    ("seg", "ending"),
]

# 前半／後半それぞれの置き場所。RadioDJの日付マスクは同じ `yyyy-MM-dd.HH` のまま、
# **フォルダを2つに分けて**イベントを2つ置く（マスクを増やさなくて済む）。
PL_DIR = {"a": "playlists", "b": "playlists_b"}

# ============================================================
# ★★ジングル（2026-09-06 nordw
#   「いろんなサイバー演歌歌手のバージョンがあって、**なにが流れるかが局のたのしみの
#     ひとつにもなってる**んだよ。楽曲と一緒においてあるはず」）
#
# ★2026-09-06 プロハンさん回答で確定。曲とは**別フォルダ**だった。
JINGLE_DIR = r"C:\CYBER_ENKA_STREAM\jingles"
# JINGLE_01.mp3 〜 JINGLE_12.mp3 の12本。
# ⚠★**02と07は使わない**（プロハンさん指示）。こずえのジングルだが、
#   **現在の喋り声と違う**ため。番組本編のこずえと声が変わってしまう。
JINGLES = [f"JINGLE_{i:02d}.mp3" for i in range(1, 13) if i not in (2, 7)]
# こちらが配る1本。ジングルが1本も指定されていない時の保険として残す
FALLBACK_JINGLE = "jingle1.wav"

SEP = "\\"      # ★テストでファイル名を切り出す用（本文は PureWindowsPath 任せ）
REAL_JINGLES = list(JINGLES)   # ★テストが差し替えたあと本番設定に戻すため


def _halves():
    """ORDER を ("split",) で前半・後半に割る。"""
    i = ORDER.index(("split",))
    return ORDER[:i], ORDER[i + 1:]


def half_of(block: str) -> str | None:
    """ブロック名が前半("a")か後半("b")か。ORDER に無ければ None。

    ★build.py が「欠けたときにどこまで諦めるか」を決めるのに使う。
      前半が欠けたら番組ごと配らない。**後半だけなら前半は配る**（曲へ落ちるだけ）。
      ⚠ 判定を build.py 側に写経しない。並びを変えたとき片方だけ直る事故になる。
    """
    for name, part in zip(("a", "b"), _halves()):
        for item in part:
            if (item[0] == "seg" and item[1] == block) or item[0] == block:
                return name
    return None


def _seg_path(outdir: Path, name: str) -> str | None:
    """seg_XX_<name>.(wav|mp3) を名前で探す。

    ★番号で決め打ちしない。番号はコーナーが増えるたびにずれる。
      make_through.py で同じ理由の事故を2回やっている（SAを足した時とニュースを足した時）。
    ⚠★拡張子でも決め打ちしない。**本番(runner/build.py)は .wav、ローカル(generate_v2.py)は
      .mp3** を作る。最初 .wav 決め打ちで書いて、ローカル検証が全滅した（2026-09-05）。
    """
    for ext in ("wav", "mp3"):
        hits = sorted(outdir.glob(f"seg_*_{name}.{ext}"))
        if hits:
            return str(hits[0])
    return None


def build(day: datetime.date, outdir: Path, pack: dict, log,
          hours=BROADCAST_HOURS, voice_root: str = VOICE_ROOT,
          song_dir: str = SONG_DIR, halves=("a", "b")) -> list[Path]:
    """その日のM3Uを放送回ごとに書く。書けたファイルの一覧を返す。

    ★VPS上のパスで書く。ここ(Actions)のパスではない。
      ローカルの outdir は `out/2026-09-05/` だが、VPSでは
      `C:\\kozue_asa\\2026-09-05\\` に降りてくる。

    halves: 書く側を選ぶ。パスAは両方、**パスBは後半だけ**（前半は既に放送済み・
            書き換えても読み直されないので触らない）。
    """
    def vps(p: str) -> str:
        r"""ここのパスを、VPS上のパスに読み替える。

        ★★`Path` を使ってはいけない（2026-09-05 実際に納品物が壊れた）。
          本番のランナーは **Linux** なので `Path` は POSIX 版になり、区切りが `/` になる。
          結果、納品されたM3Uが `C:\kozue_asa/2026-09-05/seg_01_talk1.wav` という
          **区切りの混ざったパス**になっていた。Windowsは大抵通すが、RadioDJは
          フォーラムに「ファイル名にうるさい(finicky)」という報告がある。賭けない。
          → `PureWindowsPath` なら**どのOSで動かしても `\` で書く**。
        ⚠ ログの数字（M3U 11行）は正しかった。**中身は実物を取って見るまで分からない。**
        """
        return str(PureWindowsPath(voice_root) / day.isoformat() / Path(p).name)

    def render(part) -> tuple[list[str], list[str]]:
        """並びを行に落とす。(行, 欠けているもの) を返す。"""
        lines, missing = ["#EXTM3U"], []
        jingle_i = 0        # この半分の中で何本目のジングルか
        for item in part:
            kind = item[0]
            if kind == "seg":
                p = _seg_path(outdir, item[1])
                if not p:
                    missing.append(item[1])
                    continue
                lines.append(vps(p))
            elif kind == "mail":
                # ★パスBの本番版があればそれを使う。無ければパスAのフォールバック版
                p = _seg_path(outdir, "mail") or _seg_path(outdir, "mail_fb")
                if not p:
                    missing.append("mail")
                    continue
                lines.append(vps(p))
            elif kind == "jingle":
                # ★日付フォルダの中に置く。out/直下に1回だけ置くと
                #   `r2.py prune --days 7` が更新日で消してしまう（毎日納品すれば消えない）。
                # ★★2026-09-06: ジングルは**サイバー演歌歌手ごとに何種類もある**
                #   （nordw「なにが流れるかが局のたのしみのひとつにもなってる」）。
                #   ⚠ ランダムにしない。**日付から決める**ので、同じ日に何度作り直しても
                #     同じになる（パスAとパスBで食い違わない／あとから再現できる）。
                #   ★1回の放送に2回出るので、**2回目は次のもの**にする（同じ回で連続しない）。
                nj = len(JINGLES)
                j = JINGLES[(day.toordinal() * 2 + jingle_i) % nj]
                # ⚠ jingle1.wav（こちらが配る唯一の1本）だけは日付フォルダの中。
                #   歌手ぶんのジングルは**曲と同じフォルダ**にある（＝こちらは配らない）。
                lines.append(str((PureWindowsPath(voice_root) / day.isoformat() / j)
                                 if j == FALLBACK_JINGLE
                                 else (PureWindowsPath(JINGLE_DIR) / j)))
                jingle_i += 1
            else:   # song1 / song2
                s = pack.get(kind) or {}
                f = s.get("file")
                if not f:
                    missing.append(kind)
                    continue
                lines.append(str(PureWindowsPath(song_dir) / f))
        return lines, missing

    parts = dict(zip(("a", "b"), _halves()))
    made = []
    for half in halves:
        pl_dir = outdir.parent / PL_DIR[half]
        pl_dir.mkdir(parents=True, exist_ok=True)
        lines, missing = render(parts[half])
        if missing:
            # ★欠けたまま書かない。**欠けた番組は流さない**（build.py と同じ方針）。
            #   M3Uが無ければ RadioDJ は何も差し込まず、曲が流れ続ける。
            # ★2026-09-06: 前半・後半で独立に判断する。**後半が欠けても前半は流す。**
            #   （1本だった頃は、占いが1本欠けただけで番組が全滅していた）
            log.error("★%s のM3Uは書かない。欠けている: %s", PL_DIR[half], missing)
            continue
        for h in hours:
            # マスク `yyyy-MM-dd.HH` に合わせる
            p = pl_dir / f"{day.isoformat()}.{h:02d}.m3u"
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
            made.append(p)
            log.info("M3U %s/%s (%d行)", PL_DIR[half], p.name, len(lines) - 1)
    return made


def _test(log) -> None:
    """壊れたら落ちる最小のチェック。`python v2/playlist.py --test`

    ★ここが静かに間違うと**放送の並び順が壊れる**が、聴くまで気づけない。
      本番を15分回す前に、ここで潰せるものは潰す。
    """
    import shutil, tempfile
    day = datetime.date(2026, 9, 5)
    pack = {"song1": {"file": "A.mp3"}, "song2": {"file": "B.mp3"}}
    tmp = Path(tempfile.mkdtemp()) / day.isoformat()
    tmp.mkdir(parents=True)
    try:
        # 本番と同じ 7ブロック・.wav（★ローカルは.mp3。両方拾えないと片方で全滅する）
        for i, n in enumerate(
                ["talk1", "traffic", "sa", "news", "mail_fb", "uranai", "ending"], 1):
            (tmp / f"seg_{i:02d}_{n}.wav").write_bytes(b"x")

        made = build(day, tmp, pack, log)
        assert len(made) == 6, f"パスAは前半3本＋後半3本の6本書くはず: {made}"
        A = {p for p in made if p.parent.name == "playlists"}
        B = {p for p in made if p.parent.name == "playlists_b"}
        assert len(A) == 3 and len(B) == 3, (A, B)

        ta = sorted(A)[0].read_text(encoding="utf-8")
        tb = sorted(B)[0].read_text(encoding="utf-8")
        # ★前半に**おたよりが入っていない**こと。ここが崩れると放送中の差し替えが効かない
        assert "mail" not in ta, f"おたよりが前半に混ざっている:\n{ta}"
        assert "seg_05_mail_fb.wav" in tb, "後半はフォールバック版おたよりを指すはず"
        # ★ジングルは曲明けの2回。**両方とも前半**（ファイル名で数えない。実名は変わる）
        assert len([l for l in ta.splitlines() if l.startswith(JINGLE_DIR)]) == 2, ta
        assert not any(l.startswith(JINGLE_DIR) for l in tb.splitlines()), tb
        assert ta.index("A.mp3") < ta.index("B.mp3"), "曲の順番が入れ替わっている"
        # ★前半＋後半で7ブロック全部が1回ずつ出ること（割った拍子に落とさない）
        both = ta + tb
        for n in ["talk1", "traffic", "sa", "news", "mail", "uranai", "ending"]:
            assert both.count(f"_{n}") == 1, f"{n} が {both.count(f'_{n}')} 回"
        # ★Linuxのランナーで走らせても Windows のパスで書けているか
        #   （ここを見ていなかったので、区切りが混ざったM3Uを一度納品した）
        for t in (ta, tb):
            for ln in t.splitlines()[1:]:
                assert "/" not in ln, f"区切りが混ざっている: {ln}"
                assert ln[1:3] == ":\\", f"絶対パスになっていない: {ln}"

        # パスB: 本番版おたよりが来たら差し替わる。★**後半しか書き直さない**
        (tmp / "seg_01_mail.wav").write_bytes(b"x")
        b = build(day, tmp, pack, log, hours=[7], halves=["b"])
        assert len(b) == 1 and b[0].parent.name == "playlists_b" \
            and b[0].name.endswith(".07.m3u"), b
        t7 = b[0].read_text(encoding="utf-8")
        assert "seg_01_mail.wav" in t7 and "mail_fb" not in t7, "おたよりが差し替わっていない"
        # ★前半は触っていないこと（放送開始時に読まれてしまっているので書き換え禁止）
        assert sorted(A)[0].read_text(encoding="utf-8") == ta, "パスBが前半を書き換えた"

        # 欠けたら書かない（＝配らない＝曲が流れ続ける）
        # ★★2026-09-06: **後半が欠けても前半は書く**。片方だけ諦める
        (tmp / "seg_06_uranai.wav").unlink()
        m = build(day, tmp, pack, log, hours=[6])
        assert [p.parent.name for p in m] == ["playlists"], \
            f"後半が欠けたのに前半まで止めた／後半を書いた: {m}"
        # 前半が欠けたら、その回は前半ごと出さない
        (tmp / "seg_04_news.wav").unlink()
        m = build(day, tmp, pack, log, hours=[6])
        assert m == [], f"欠けているのに書いてしまった: {m}"

        # ★half_of: build.py が「どこまで諦めるか」をこれで決める。
        #   ⚠ ここが嘘をつくと、後半の欠けで番組ごと止まる／前半の欠けを配ってしまう
        assert half_of("news") == "a" and half_of("song1") == "a", "前半の判定が違う"
        assert half_of("mail") == "b" and half_of("ending") == "b", "後半の判定が違う"
        assert half_of("そんなブロックは無い") is None

        # ★ジングル: **JINGLESに名前を並べるだけで日替わりで回り始める**こと。
        #   ⚠ ここが効かないと、プロハンさんから名前をもらった日に
        #     「並べたのに回らない」で詰まる。名前が来る前に通しておく。
        for n in ("seg_04_news.wav", "seg_06_uranai.wav"):
            (tmp / n).write_bytes(b"x")          # さっき消したので戻す
        g = globals()
        g["JINGLES"] = ["Jア.mp3", "Jイ.mp3", "Jウ.mp3"]
        try:
            got = {}
            for dd in (datetime.date(2026, 9, 5), datetime.date(2026, 9, 6),
                       datetime.date(2026, 9, 7)):
                t = build(dd, tmp, pack, log, hours=[6], halves=["a"])[0] \
                    .read_text(encoding="utf-8")
                js = [ln for ln in t.splitlines() if ln.rsplit(SEP, 1)[-1] in JINGLES]
                assert len(js) == 2, f"ジングルが2本出ていない: {js}"
                assert js[0] != js[1], f"同じ回で同じジングルが2回: {js}"
                # ★歌手ぶんは**曲と同じフォルダ**を指すこと（こちらは配らないので）
                for ln in js:
                    assert ln.startswith(JINGLE_DIR), f"ジングルフォルダを指していない: {ln}"
                got[dd.isoformat()] = tuple(js)
            assert len(set(got.values())) == 3, f"日替わりになっていない: {got}"
            # ★保険: JINGLESが空なら、こちらが配る1本（日付フォルダ側）に落ちる
            g["JINGLES"] = [FALLBACK_JINGLE]
            t = build(day, tmp, pack, log, hours=[6], halves=["a"])[0] \
                .read_text(encoding="utf-8")
            assert t.count("jingle1.wav") == 2, "保険の1本に落ちていない"
            assert all(ln.startswith(VOICE_ROOT) for ln in t.splitlines()
                       if ln.endswith("jingle1.wav")), "保険の1本は日付フォルダのはず"
        finally:
            g["JINGLES"] = REAL_JINGLES

        # ★★本番の設定そのもので確認する（テスト用の偽名で通しても意味がない）
        #   ⚠ 02と07は**こずえの旧声**なので絶対に出てはいけない（プロハンさん指示）
        assert "JINGLE_02.mp3" not in JINGLES and "JINGLE_07.mp3" not in JINGLES
        assert len(JINGLES) == 10, JINGLES
        seen = set()
        for k in range(14):     # 2週間ぶん回して、10本すべてが出るか
            dd = datetime.date(2026, 9, 6) + datetime.timedelta(days=k)
            t = build(dd, tmp, pack, log, hours=[6], halves=["a"])[0] \
                .read_text(encoding="utf-8")
            js = [ln.rsplit(SEP, 1)[-1] for ln in t.splitlines()
                  if ln.startswith(JINGLE_DIR)]
            assert len(js) == 2 and js[0] != js[1], f"{dd}: {js}"
            seen.update(js)
        assert seen == set(JINGLES), f"出ないジングルがある: {set(JINGLES) - seen}"
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)
    print("★playlist: 全部通った")


if __name__ == "__main__":
    import json, logging, sys
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "--test" in sys.argv:
        _test(logging.getLogger("m3u"))
        raise SystemExit(0)
    base = Path(__file__).resolve().parent.parent
    d = datetime.date.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2026-09-05")
    outdir = base / "episodes_v2" / d.isoformat()
    pack = json.loads((outdir / "datapack.json").read_text(encoding="utf-8"))
    made = build(d, outdir, pack, logging.getLogger("m3u"))
    for p in made:
        print(f"\n=== {p.name} ===")
        print(p.read_text(encoding="utf-8"))
        break
