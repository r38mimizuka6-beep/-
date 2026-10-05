"""所見の文章化（任意機能）。

数字の計算・判定はすべて決定論的なコードが行う。ここがやるのは、
出来上がった判定を読んで文章にすることと、検索結果の貼り付けから
項目を拾うことだけ。APIキーが無ければ何もせず None を返し、
ダッシュボードは計算結果だけを表示する。

ここで数値を作らせてはいけない。渡した数値をそのまま使わせる。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

MODEL = "claude-opus-5-5"
MAX_TOKENS = 4000


def available() -> tuple[bool, str]:
    """SDKと資格情報が揃っているか。"""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "anthropic パッケージが入っていません（pip install anthropic）"
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True, "APIキーを環境変数から読み込みます"
    return (False,
            "ANTHROPIC_API_KEY が設定されていません。"
            "設定しない場合も、計算結果はそのまま表示されます（文章化だけが省かれます）")


@dataclass
class LlmResult:
    ok: bool
    text: str = ""
    data: dict | None = None
    error: str = ""


def _call(system: str, user: str, *, want_json: bool = False) -> LlmResult:
    ok, msg = available()
    if not ok:
        return LlmResult(False, error=msg)
    import anthropic

    client = anthropic.Anthropic()
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.AuthenticationError:
        return LlmResult(False, error="APIキーが無効です")
    except anthropic.RateLimitError:
        return LlmResult(False, error="レート制限に達しました。少し待って再実行してください")
    except anthropic.APIStatusError as e:
        return LlmResult(False, error=f"APIエラー（{e.status_code}）: {e.message}")
    except anthropic.APIConnectionError:
        return LlmResult(False, error="外部へ接続できませんでした")

    if resp.stop_reason == "refusal":
        return LlmResult(False, error="モデルが応答を拒否しました")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not want_json:
        return LlmResult(True, text=text)
    body = text
    if "```" in body:
        body = body.split("```")[1]
        body = body[4:] if body.startswith("json") else body
    try:
        return LlmResult(True, text=text, data=json.loads(body))
    except json.JSONDecodeError:
        return LlmResult(False, text=text, error="JSONとして読めませんでした")


SYSTEM_REVIEW = """\
あなたは小売チェーンの売り場担当者を助ける分析アシスタントです。
渡されたJSONは、すでに計算が終わった予実検証の結果です。

守ること:
- 数値は渡されたものだけを使い、自分で計算し直したり丸めたりしない。
- 渡されていない事実を足さない。原因は「候補」として挙げ、断定しない。
- 既存店は5店しかなく、予測は仮説である前提を崩さない。
- 日本語で、売り場担当者がそのまま読める言葉で書く。
- 「どのカテゴリーを、いつ、どう確かめるか」が分かる形で終える。
"""

SYSTEM_EXTRACT = """\
あなたは、検索結果の貼り付けテキストから店舗の属性を拾う抽出器です。

守ること:
- テキストに書かれていないことは null にする。推測で埋めない。
- 数値は単位を揃える（距離はメートル、面積は平方メートル、台数は整数）。
- 指定されたJSONだけを返す。説明文は付けない。
"""


def review_verification(payload: dict) -> LlmResult:
    """予実検証の結果に、所見と次アクションを付ける。"""
    user = (
        "次は予実検証の結果です。これを読んで、\n"
        "1) 全体として想定どおりか\n"
        "2) 気にすべきカテゴリーと、その理由（候補）\n"
        "3) 来週までに確かめること\n"
        "を、それぞれ数行で書いてください。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    return _call(SYSTEM_REVIEW, user)


EXTRACT_FIELDS = [
    "address", "open_date", "nearest_station", "nearest_station_m",
    "parking_spaces", "sales_floor_sqm", "store_format",
    "comp_sm_1km", "comp_cvs_1km", "comp_drug_1km",
    "nearest_comp_name", "nearest_comp_m", "fac_school", "fac_nursery",
    "fac_university",
]


def extract_search_fields(store_name: str, pasted: str) -> LlmResult:
    """検索結果の貼り付けから、埋められる項目だけを拾う。"""
    user = (
        f"店舗名: {store_name}\n\n"
        "次の貼り付けテキストから、分かる項目だけを拾ってJSONで返してください。\n"
        f"キー: {EXTRACT_FIELDS}\n"
        "分からない項目は null にしてください。\n\n"
        "--- 貼り付けテキスト ---\n" + pasted[:20000]
    )
    return _call(SYSTEM_EXTRACT, user, want_json=True)
