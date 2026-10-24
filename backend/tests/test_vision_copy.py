"""Vision shot notes and copy grounded in them (models faked; no network)."""

from __future__ import annotations

import json

from app import llm
from app.creative import vision
from app.creative.copywriter import CopyRequest, LLMCopywriter, ProductInfo, TemplateCopywriter, HOOKS, similarity

VISUAL = {"subject": "红帝王蟹", "matches_label": True, "count": "一池", "shot_size": "近景",
          "actions": ["蟹腿一节节往前挪"], "details": ["壳上一排排尖刺"], "highlight": "一只蟹正对镜头触须微动"}


def test_shot_notes_keep_only_what_a_picture_can_show():
    notes = vision.normalise({"subject": "红帝王蟹", "matches_label": "yes", "shot_size": "超近",
                              "actions": ["蟹腿缓慢往前挪", "今天刚到港的蟹", "气泡不断往上冒"],
                              "details": ["壳很新鲜", "腿上有划痕", "紫红色外壳带尖刺", "最好的品质"],
                              "highlight": "蟹钳张开", "curiosity": ["这蟹是死的吗"], "usable": True})
    assert notes["actions"] == ["蟹腿缓慢往前挪", "气泡不断往上冒"]
    assert notes["details"] == ["紫红色外壳带尖刺"] and notes["curiosity"] == []
    assert notes["matches_label"] is None and notes["shot_size"] == ""
    analysis = {"vision": {"version": vision.VERSION, "shots": [
        {"start": 0.0, "end": 4.0, **notes}, {"start": 4.0, "end": 9.0, "error": "timeout"}]}}
    assert vision.notes_for(analysis, 1.0, 3.0)["highlight"] == "蟹钳张开"
    assert vision.notes_for(analysis, 5.0, 7.0) is None  # failed shots are never used
    assert vision.notes_for({"vision": {"version": "old", "shots": []}}, 0, 1) is None


class FakeModel:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def chat(self, messages, **kw):
        self.calls.append(messages)
        return json.dumps(self.answers.pop(0), ensure_ascii=False)


def _req(**kw):
    slots = [{"caption": None, "label": None, "item": "红帝王蟹", "role": "hook", "seconds": 2.5, "visual": VISUAL},
             {"caption": "selling_point", "label": "badge", "item": "红帝王蟹", "role": "detail", "seconds": 2.0,
              "visual": VISUAL},
             {"caption": "cta", "label": None, "item": "红帝王蟹", "role": "ending", "seconds": 2.6}]
    return CopyRequest(ProductInfo(items=["红帝王蟹"], store="渔港"), "hook_reveal", "douyin", slots, seed=4, **kw)


def _answer(**over):
    data = {"hooks": ["今天到港的帝王蟹", "这排尖刺\n是干嘛用的？", "盯着这只蟹的触须"],
            "captions": [{"text": None, "facts": []}, {"text": "腿一节节往前挪", "facts": ["F2-1"]},
                         {"text": "想看哪只扣1", "facts": []}],
            "labels": [None, {"text": "看尖刺", "facts": ["F2-2"]}, None], "titles": ["帝王蟹的尖刺有啥用", "跟着镜头看帝王蟹"],
            "description": "镜头凑近红帝王蟹，壳上的尖刺一排排。", "hashtags": ["帝王蟹", "海鲜档口"]}
    data.update(over)
    return data


def test_vision_copy_is_grounded_novel_and_claim_free(monkeypatch):
    fake = FakeModel([_answer()])
    monkeypatch.setattr(llm, "text_model", lambda: fake)
    recent = ["盯着这只蟹的触须看"]
    r = LLMCopywriter().compose(_req(recent=recent))
    assert r.source == "llm_vision" and r.angle
    assert "到港" not in r.hook  # unprovable claim rejected
    assert r.hook == "这排尖刺\n是干嘛用的？"  # the stale candidate loses to the novel one
    assert r.captions == [None, "腿一节节往前挪", "想看哪只扣1"] and r.labels[1] == "看尖刺"
    sent = json.loads(fake.calls[0][1]["content"])
    assert sent["镜头"][1]["画面事实"][0].startswith("F2-1 ") and "price" not in sent["产品信息"]


def test_uncited_captions_are_repaired_then_fall_back(monkeypatch):
    bad = _answer(captions=[{"text": None}, {"text": "肉质饱满很好吃", "facts": ["F2-1"]}, {"text": "关注我"}],
                  hooks=["今天到港"])
    fake = FakeModel([bad, _answer(captions=[{"text": None}, {"text": "壳上尖刺一排排", "facts": ["F1-2"]},
                                             {"text": "关注我"}])])
    monkeypatch.setattr(llm, "text_model", lambda: fake)
    r = LLMCopywriter().compose(_req())
    assert len(fake.calls) == 2 and "以下问题需要改正" in fake.calls[1][-1]["content"]
    # second answer is usable, but its caption cites another shot's fact: that one caption is replaced
    assert r.captions[1] != "壳上尖刺一排排" and any("画面事实" in w for w in r.warnings)


def test_template_copy_rotates_instead_of_failing_when_hooks_are_used_up():
    pool = HOOKS["asmr_immersive"]
    r = TemplateCopywriter().compose(CopyRequest(ProductInfo(items=["东星斑"]), "asmr_immersive", "douyin",
                                                 [{"caption": None, "label": None, "item": "东星斑"}],
                                                 avoid_hooks=set(pool), seed=1))
    assert r.hook and r.warnings
    assert similarity("红帝王蟹到了", "红帝王蟹到了") == 1.0 and similarity("帝王蟹", "东星斑") == 0.0


def test_cliches_and_ungrounded_badges_never_reach_the_screen(monkeypatch):
    answer = _answer(hooks=["现捞现做\n今日到港", "数数这缸\n有几条〖腿〗在动", "家人们看这蟹"],
                     labels=[None, "现捞现做", None])
    monkeypatch.setattr(llm, "text_model", lambda: FakeModel([answer]))
    r = LLMCopywriter().compose(_req())
    assert r.hook == "数数这缸\n有几条〖腿〗在动"  # emphasis survives for the renderer
    assert r.labels[1] is None  # no sticker rather than a generic one
    assert "〖" not in r.title + r.description


def test_template_fallback_is_built_from_the_shot_notes():
    visual = {**VISUAL, "curiosity": ["这排尖刺是干嘛用的？"]}
    slots = [{"caption": None, "label": None, "item": "红帝王蟹", "visual": visual},
             {"caption": "selling_point", "label": "badge", "item": "红帝王蟹", "visual": VISUAL}]
    r = TemplateCopywriter().compose(CopyRequest(ProductInfo(items=["红帝王蟹"]), "hook_reveal", "douyin", slots, seed=2))
    assert r.hook.replace("\n", "") == "这排尖刺是干嘛用的？" and r.hook_key == "vision"
    assert r.captions[1] in (VISUAL["highlight"], *VISUAL["actions"], *VISUAL["details"])
    assert r.labels[1] in (None, *[x for x in VISUAL["actions"] + VISUAL["details"] if len(x) <= 5])
