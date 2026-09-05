# -*- coding: utf-8 -*-
"""「電脳芸能ニュース」の骨組みを作る（2026-09-05 nordw指示・2回目の設計）。

★1回目は捨てた。汎用の芸能ニュース型（新曲/対バン/受賞/私生活…）を私の頭で作り、
  格付けを曲数で決めていた。nordwに2度止められた:
    「そういう単純な話じゃない　世界感があるんだ」
    「あんま数ではかんないほうがおもしろい　少なくても濃いのもいる」
  ∴ **型は世界から作る。** 下の KATA は全部 world.json に書いてある設定が根拠。
    汎用の芸能ゴシップ型は1つも入れない。

★世界の形（world.json 参照）
  - サイバー演歌はいろんな作り手がいて、その人その人がプロダクションを持つ。
    引火帝国はそのひとつ（オリジネーター）。**傘下ではなく並立**。
  - こずえ・汐見さち・花村紅は**別々の作り手のキャラなのに仲良し**。
    ここが「他所のキャラを出す理由」になっていて、配分の数字より強い。

★ニュースの大きさ（_フォーマット_電脳芸能人紳士録 より）
  マグマ☆シゲルの「ロケ弁の数が明らかに足りなくて喧嘩したね」級が正解。**大事件は要らない。**

★他所のキャラの扱い（nordw 2026-09-05）「悪口じゃなければよい」
  → CEBDR24に曲がある人は出してよい。ただし**悪口は一切書かない**。

★世界は育てる。使った型と面々を state/geinou_chronicle.json に貯め、翌日以降の前提にする。
  骨組みはこちらが決定論で決め、LLMには肉付けだけさせる（年代記が常に正確になる）。
"""
import datetime
import json
import random
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CHRONICLE = BASE / "state" / "geinou_chronicle.json"
KEEP_DAYS, SHOW_PAST = 120, 8
# ★汎用の受け皿。これを寝かせると候補が枯れる（2026-09-05 実測）
NO_COOLDOWN = {"新曲", "ひとこと"}

# ★★定番ネタのレア度。**ここだけ動かせば全部の定番が一斉に増減する。**
#   型ごとに数字をいじるより、つまみが1個のほうが後で扱いやすい。
#   小さくするほどレアになる。1.0 = KATAに書いた重みそのまま。
#   nordw（2026-09-05）「そこそこレアなくらいがちょうどいい」→「月1〜2回にする」
#   実測: 1.0 のとき定番は月3〜4.5回、0.4 で月1〜2回に落ちる。
TEIBAN_RARITY = 0.4

# ★★育てかた（nordw 2026-09-05「1からだんだん3に育てる」）
#   いま受け皿（新曲・ひとこと）が6割強を占める。これは**レア度の問題ではなく、
#   定番型が10個しかない＝設定の埋まっている人が10人しかいない**から。
#   ∴ 薄めた定番を無理に濃くするのではなく、**世界が埋まるたびに KATA に型を足す。**
#   人が増えれば受け皿の出番が自然に減って、比率は勝手に下がる。
#   → `python v2/geinou.py --sodate` で「カードはあるのに型が無い人」が出る。そこが次の一手。


def _find(name: str) -> Path:
    for d in ("v2", "data", "assets"):
        p = BASE / d / name
        if p.exists():
            return p
    raise FileNotFoundError(name)


# 出来事の型。★すべて world.json に根拠がある。cast は台帳の name と一字一句そろえる。
# (型名, 出す面々, 書き方の指示, 重み)
# ★重み＝その型の出やすさ。**ここが直接いじるつまみ**。下の実測（30日）を見て動かす。
#   ★★定番ネタは「そこそこレアなくらいがちょうどいい」（nordw 2026-09-05）。
#     私は一度これを取り違えて「看板ネタが0回なのは配分ミス」と判断し、
#     『今日も目覚めず』を5に上げた。**上げすぎ。**毎週来る定番はもう定番ではない。
#     月に1〜2回、忘れた頃に出るのが正しい。値を戻した。
#   受け皿（新曲・ひとこと）だけは薄くしておく。厚くすると世界固有のネタが押し出される
#   （実測: 新曲=5/ひとこと=4 にしたら受け皿だけで45%を占めた）。
KATA = [
    # ⚠複数人の型は「最近出た人」チェックを通らない（1人の型にしか掛けていない）ので
    #   同じ重みでも出やすい。実測で三人組=月3.7／禁断の師弟=月4.3。重みを半分にして揃えた。
    ("今日も目覚めず", ["万正男"],
     "熱海のリゾートマンション地下でコールドスリープ中。今日も目覚めない。"
     "★『目覚めるか……！？』と煽って**必ず空振りさせる**。目覚めさせない。ここが崩れるとネタが死ぬ。", 3),
    ("脳波の新曲", ["万正男"],
     "眠ったまま、脳波だけで新曲が出た。曲名は与えられた実在の曲を使う。", 2),
    ("また復活した", ["二条静馬"],
     "死去するものの同日中に復活する人。今回も戻ってきた。"
     "★万正男と対にして触れてよい（片方は目覚めず、片方は死んでも戻る）。", 2),
    ("三人組", ["雷音こずえ", "汐見さち", "花村紅"],
     "★性格もキャラも全然違うのに妙にうまが合う3人。**別々のプロダクション**なのが味。"
     "一緒にいた、という話だけで成立する。こずえは当事者なので自分の話として語れる。", 1),
    ("ニセモノ出没", ["電音ゴリエとインカテー国"],
     "こずえ・アッサム吉岡・猿一郎のニセモノ3人組。"
     "★**音では『引火帝国』と区別がつかない**ので、必ず『ニセモノのほう』と分かる補足を入れる。"
     "こずえは自分の偽物の話をすることになる＝一番おいしい。", 3),
    ("ジョーカー", ["幸雪三"],
     "青森の大御所。場を荒らして帰っていく。★笑いは**幸雪三の側に落とす**。"
     "絡まれた側を笑わない。具体的な言動は描写しない。", 2),
    ("悪役俳優", ["マグマ☆シゲル"],
     "MVの常連の悪役俳優。★昔の芸能キャリアの**しょうもない細部**を一つ拾う"
     "（ロケ弁が足りなかった、台本には通行人Bと書いてあった等）。最後に妙に深い名言で締まる。", 3),
    ("愛の人", ["しずゑさん"],
     "82歳、愛の化身。恋人はお釈迦様。★**さんまでが名前**なので敬称を足さない。"
     "こずえは茶化さず、本気で受け止めて感心する側に立つ。★病因には一切触れない。", 2),
    ("予算がない", ["倉本タマ"],
     "倉本義男の飼い猫AI。★**予算がない時に登場する**。歌詞がほぼ「ニャ」。"
     "出てきた事実そのものが、局の台所事情の話になる。", 3),
    ("ニュースにならない正統派", ["遠野とわ"],
     "**正統派ゆえに、こういうときに弱い。**このコーナーは変わったやつが得をする"
     "（蟹、眠り続ける不動産王、ニセモノ、82歳の愛の化身）。正統派には拾うネタが無い。"
     "★★**けなさない。**「地味」「印象が薄い」は禁止。**うまいのにネタが無い**という形にする。"
     "こずえは完全に味方。オチは『変な奴ばかりのこの世界のほうがおかしい』の向きに落とす。"
     "★持ち歌に『バズれ！』『サイバー演歌道』がある。使うと効く。", 3),
    ("サイバー念写", ["金切みそら"],
     "**サイバー念写の使い手**の少女演歌歌手。ミュータントとの噂もある。"
     "★念写は確定した設定なので普通に言ってよい。**ミュータントは噂のまま**（断定させない）。"
     "★★**少女のキャラ。色恋の噂・容姿の話に一切乗せない。**"
     "★持ち歌の『ADHD無双2026』は曲名として読むだけ。ADHDそのものについて語らせない。", 3),
    ("蟹", ["蟹江よしえ"],
     "**蟹**。地球外からきたという噂もある。"
     "★蟹であることは確定なので普通に言ってよい。**地球外は噂のまま**（「〜だとも言われてる」まで）。"
     "★曲名がそのまま証拠になっている（宇宙のカニ缶／空飛ぶ円盤に弟が乗ったよ／サイバーカニ化）。"
     "★カバー曲は実在のアーティストの曲。元の歌手について語らない。", 3),
    ("流れ着いた男", ["スヌーピー犬太郎"],
     "記憶を失って青森の浜に流れ着き、幸雪三に拾われた。"
     "★正体の噂は**『〜だとも言われてる』の形を絶対に崩さない**。断定させない。"
     "★人種を属性として口に出さない。", 2),
    ("禁断の師弟", ["朝氷川ヨシキ", "二条静馬"],
     "旭川の陣営の親玉（作曲家）と、その所属歌手。**禁断の師弟関係のうわさ**がある。"
     "★★**噂の形を絶対に崩さない**（「〜だとか」「〜って噂よ」まで）。確定させたらネタが終わる。"
     "★二条静馬は作曲家なので師弟は仕事の話。**『禁断の』を付ける言い方自体が笑いどころ。**"
     "★下世話にしない。こずえは面白がるが詮索しない。どちらの悪口にもしない。"
     "★★**この噂はこの二人だけ。**似た噂を別の相手に広げない。女性キャラは巻き込まない。", 1),
    ("殴らない番長", ["西馬拳二郎"],
     "サイバー演歌番長。異名はデジタル長渕。**過去は荒れていた**が、いまは『怒りを鎮める男』。"
     "流血しても歌うが、殴らない。一人称は「俺」。"
     "★荒れていた過去を**武勇伝にしない**。具体の暴力を描写しない。「昔はいろいろあった人」まで。"
     "★効くのは『いまは殴らない』のほう。こずえは一目置いている側で、茶化さない。", 3),
    ("堅物のショーグン", ["倉本義男"],
     "異名はCYBER ENKA SHOGUN。**堅物**。倉本タマ（歌詞がほぼ「ニャ」の飼い猫AI）の飼い主。"
     "★堅物がまじめにやっているのに、細部がしょうもない——という落差で書く。"
     "★けなさない。こずえは呆れながらも一目置いている。", 3),
    # ★育てた1歩目（2026-09-05）。アッサム吉岡に設定が付いたので型にした。
    #   これを繰り返して受け皿の比率を下げていく（--sodate で次の候補が出る）。
    ("海を渡った男", ["アッサム吉岡"],
     "インドでトラックの運転手をしていたが、日本の演歌に惚れ込んで海を渡ってきた。異名は炎のマサラこぶし。"
     "★**憧れた歌手の名前は出さない**（実在の方）。「日本で活躍したインド人の演歌歌手」までにする。"
     "★インドを『変わった国』として扱わない。惚れ込んで海を渡った、という筋だけで十分。", 3),
    ("新曲", None,
     "その人の新曲の話。★曲名は与えられた実在の曲だけを使う。こずえに曲名を作らせない。"
     "★台帳に『音楽的特徴』があれば**そこを語る**（作風・歌い方・歌の中身）。"
     "キャラが立っていない人でも、音楽の話なら中身が出る。", 2),
    ("ひとこと", None,
     "その人が何か言っていた、という程度の小さい話。★**大事件にしない。**"
     "『ロケ弁が足りなかった』くらいの大きさでちょうどよい。"
     "★台帳に『音楽的特徴』『立場』があればそこを絡める。", 2),
]


def _world() -> dict:
    return json.loads(_find("world.json").read_text(encoding="utf-8"))


def _songs_by_artist() -> dict:
    songs = json.loads(_find("songs.json").read_text(encoding="utf-8"))
    by = {}
    for s in songs:
        by.setdefault(s["artist"], []).append(s["title"])
    return by


def load_chronicle() -> list[dict]:
    if not CHRONICLE.exists():
        return []
    try:
        return json.loads(CHRONICLE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_chronicle(rows, day):
    cut = (day - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    CHRONICLE.parent.mkdir(parents=True, exist_ok=True)
    CHRONICLE.write_text(json.dumps([r for r in rows if r["date"] >= cut],
                                    ensure_ascii=False, indent=1), encoding="utf-8")


def build(day: datetime.date, log, n: int = 2) -> dict | None:
    """今日の電脳芸能ニュースの骨組みを n 本。作れなければ None（コーナーを飛ばす）。"""
    try:
        world, by = _world(), _songs_by_artist()
    except Exception as e:
        log.warning("電脳芸能ニュース: 台帳か曲リストが読めない → 飛ばす: %s", e)
        return None

    ng = {x["name"] for x in world["_出してはいけない名前"]["名前"]}
    # ★出せる人 = 台帳に載っていて、CEBDR24に曲がある人（＋曲が無くても台帳の主要人物）
    cards = {p["name"]: p for p in world["人物"] if p["name"] not in ng}
    weights = world["_登場バランス"]["配分の目安"]

    past = load_chronicle()
    recent = {c for r in past[-10:] for c in r.get("cast", [])}
    # ★型にもクールダウンを置く（2026-09-05 実測でバグ）。
    #   面々だけで見ていたら「三人組」が中1日で再登場した。複数人の型は cast の
    #   重複チェックを素通りするため。定番ネタは**型ごと**寝かせるのが正しい。
    recent_kata = {r.get("kata") for r in past[-8:]}

    rnd = random.Random(f"geinou2:{day.isoformat()}")
    items, used = [], set()
    for _ in range(n):
        # ★制約は段階的に外す（2026-09-05 実測でバグ）。全部かけたまま回したら
        #   12日目に候補が枯れて0本になり、コーナーごと消えた。
        #   **番組は落とさない**のが最優先なので、詰まったら緩めて必ず1本出す。
        #   緩める順: 型のクールダウン → 人の連投チェック → 制約なし
        for relax in (0, 1, 2):
            got = False
            for _try in range(60):
                # ★定番は TEIBAN_RARITY で薄める。受け皿（NO_COOLDOWN）はそのまま
                kata, cast, hint, wt = rnd.choices(
                    KATA, weights=[k[3] if k[0] in NO_COOLDOWN else k[3] * TEIBAN_RARITY
                                   for k in KATA])[0]
                # ★汎用の受け皿（新曲・ひとこと）は塞がない。ここまで寝かせると枯れる
                if relax < 1 and kata in recent_kata and kata not in NO_COOLDOWN:
                    continue
                if cast is None:                   # 誰でもよい型 → 配分に従って引く
                    pool = [(nm, weights.get((cards[nm].get("所属") or ""), 5))
                            for nm in cards
                            if nm not in used and by.get(nm)
                            and (relax >= 2 or nm not in recent)]
                    if not pool:
                        continue
                    cast = [rnd.choices([p[0] for p in pool],
                                        weights=[max(p[1], 1) for p in pool])[0]]
                else:
                    if any(c in used for c in cast) or not all(c in cards for c in cast):
                        continue
                    if relax < 2 and len(cast) == 1 and cast[0] in recent:
                        continue                   # 定番でも連投しない
                got = True
                break
            if got:
                break
        else:
            continue                               # この1本は諦める（他の本は出す）
        used.update(cast)
        recent_kata.add(kata)              # 同じ日に同じ型を2本出さない
        # ★その人が過去に何回出たかを数える（2026-09-05）。
        #   年代記には「誰が何の型で出たか」しか無く、**何を喋ったかは残っていない**。
        #   だが「何回目か」は数えられる。それで
        #   「名前の由来・異名・経歴」のような**一度きりのネタ**の繰り返しを防げる。
        #   実際に出た問題: 西馬拳二郎の「さいばけんじろう＝サイバーが隠れてる」を
        #   毎回言うと飽きる（2026-09-05 の台本で発生）。
        appear = {c: sum(1 for r in past if c in r.get("cast", [])) + 1 for c in cast}
        items.append({
            "kata": kata, "hint": hint, "cast": cast,
            "登場回数": appear,
            # ★曲は必ず実在のものを渡す。無い人は None（曲の話をさせない）
            "songs": {c: (rnd.choice(by[c]) if by.get(c) else None) for c in cast},
            "所属": {c: cards[c].get("所属", "未確認") for c in cast},
            "台帳": {c: cards[c] for c in cast},
        })

    if not items:
        log.warning("電脳芸能ニュース: 骨組みが作れなかった → コーナーを飛ばす")
        return None
    for it in items:
        log.info("電脳芸能ニュース [%s] %s", it["kata"], "・".join(it["cast"]))

    past += [{"date": day.isoformat(), "kata": it["kata"], "cast": it["cast"]} for it in items]
    save_chronicle(past, day)
    return {
        "items": items,
        "これまでの出来事": [f'{r["date"]} {"・".join(r["cast"])} … {r["kata"]}'
                        for r in past[:-len(items)][-SHOW_PAST:]],
        "世界の語彙": world["_世界の語彙"]["語"],
        "出してはいけない名前": sorted(ng),
        "★書き方": world["_フォーマット_電脳芸能人紳士録"]["★ニュースコーナーへの落とし方"],
    }


if __name__ == "__main__":
    import logging
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING)
    log = logging.getLogger("geinou")
    if "--reset" in sys.argv and CHRONICLE.exists():
        CHRONICLE.unlink()

    if "--sodate" in sys.argv:
        # ★育てる先を出す。型を持っている人＝その人固有のネタが毎月回る人。
        #   型が無い人は汎用の受け皿でしか出番が無い＝キャラが立たない。
        w, by = _world(), _songs_by_artist()
        ng = {x["name"] for x in w["_出してはいけない名前"]["名前"]}
        has = {c for k in KATA if k[1] for c in k[1]}
        print(f"定番型 {len([k for k in KATA if k[0] not in NO_COOLDOWN])}個"
              f"／台帳の人物 {len(w['人物'])}人\n")
        print("■ カードはあるのに、まだ固有の型が無い人（＝次に型を作れる人）")
        skip = []
        for p in w["人物"]:
            if p["name"] in has or p["name"] in ng:
                continue
            # ★「型を作らない」と決めた人を候補に並べ続けない（2026-09-05）。
            #   ボギー(=BOGGIE & BURGER)は**キャラを立たせる発想が生まれる前の層**で、
            #   曲は35曲あるがニュースの登場人物ではない。
            #   ★曲があるから登場人物、とは限らない。
            if p.get("★型を作らない"):
                skip.append(p)
                continue
            n_songs = len(by.get(p.get("名義", p["name"]).split("（")[0], by.get(p["name"], [])))
            print(f"   {p['name']:14s} 曲{n_songs:3d}  所属:{p.get('所属','?')}")
        if skip:
            print("\n■ 型を作らないと決めた人（候補に出さない）")
            for p in skip:
                print(f"   {p['name']:14s} {p['★型を作らない'][:54]}…")
        print("\n■ 台帳にカードすら無い歌手（曲は多いのに設定が空）")
        # ★別名義もカード有りとして扱う。ボギー=BOGGIE & BURGER のように
        #   台帳の name と曲リストの表記が違う人がいる（2026-09-05）。
        cards = {p["name"] for p in w["人物"]}
        for p in w["人物"]:
            if p.get("名義"):
                cards.add(p["名義"].split("（")[0].strip())
        rest = sorted(((len(v), k) for k, v in by.items() if k not in cards), reverse=True)
        for n_songs, nm in rest[:8]:
            print(f"   {nm:14s} 曲{n_songs:3d}")
        print("\n★ここが埋まるほど受け皿の出番が減り、コーナーが濃くなる。")
        raise SystemExit(0)
    d0 = datetime.date.today()
    for i in range(int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 5):
        d = d0 + datetime.timedelta(days=i)
        g = build(d, log)
        print(f"--- {d} ---")
        if not g:
            print("   （今日は無し）"); continue
        for it in g["items"]:
            sg = "／".join(f"{k}『{v}』" for k, v in it["songs"].items() if v)
            print(f"  [{it['kata']}] {'・'.join(it['cast'])}"
                  + (f"  曲: {sg}" if sg else ""))
