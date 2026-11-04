/*
 * 题目数据层 —— 单一真源。
 *
 * 为什么是 .js 而不是 .json：
 *   同一份文件要在三种情况下都能加载 —— 1) 双击 index.html（file:// 下 fetch() 被 CORS 拦，
 *   script 标签不受影响）2) 静态服务器 3) 后端托管。
 * 同时后端要读它做发信前的重新计算，所以赋值号右边必须是**纯 JSON**：
 *   key 全部带引号、无注释、无尾逗号。Python 侧靠 app/content.py 剥掉
 *   `window.QUIZ_DATA = ` 前缀与末尾分号后 json.loads。
 *   改这个文件时不要把注释写进对象里面，也不要去掉引号 —— 那会让后端读不到题目。
 *
 * 逻辑层（scoring.js / scoring.py）不写死任何中文句子；文案全在 interpretations.js。
 */

window.QUIZ_DATA = {
  "meta": {
    "version": "1.0",
    "title": "喜欢还是爱",
    "subtitle": "九题自陈 · 斯滕伯格爱情三角理论",
    "targetNote": "本测试测的是你对「某一个具体的人」的感受，不是你的恋爱观。"
  },

  "dims": [
    { "key": "intimacy", "name": "亲密", "oneLiner": "愿意把真实的自己交给对方" },
    { "key": "passion", "name": "激情", "oneLiner": "身体和情绪上的渴望与起伏" },
    { "key": "commitment", "name": "承诺", "oneLiner": "在失望之后仍然选择留下" }
  ],

  "options": [
    { "value": 1, "label": "是" },
    { "value": 0, "label": "否" }
  ],

  "rules": {
    "presentThreshold": 2,
    "perDim": 3
  },

  "originals": {
    "I1": "你愿不愿意和Ta分享别人不知道的日常，你的开心、脆弱和秘密？",
    "P1": "你是否渴望亲吻、拥抱、或者触碰Ta，并会因为ta的回应而兴奋，因为Ta的冷淡而失落？",
    "C1": "如果有一天，Ta不再满足我对伴侣的期待，我是否依然愿意投入精力，尊重、关系、照顾真实的Ta？而不选择离开？"
  },

  "questions": [
    {
      "id": "I1", "dim": "intimacy", "role": "anchor",
      "text": "你愿不愿意和 Ta 分享那些别人不知道的日常 —— 你的开心、你的脆弱、你的秘密？",
      "original": "I1",
      "normalizedFrom": "补「那些」与破折号使长句可读，「开心、脆弱和秘密」并为顿号列举；均不改变语义（母题·亲密）",
      "source": "母题 · 01:14",
      "hint": "不是「我们聊得来」，而是你敢不敢把不好看的那部分给 Ta 看。"
    },
    {
      "id": "P1", "dim": "passion", "role": "anchor",
      "text": "你是否渴望亲吻、拥抱或触碰 Ta，并且会因为 Ta 的回应而兴奋、因为 Ta 的冷淡而失落？",
      "original": "P1",
      "normalizedFrom": "统一「Ta」大小写、「或者」→「或」、并列改顿号；语义未改（母题·激情）",
      "source": "母题 · 01:25",
      "hint": "重点在后半句：Ta 的反应能不能牵动你的情绪。"
    },
    {
      "id": "C1", "dim": "commitment", "role": "anchor",
      "text": "如果有一天 Ta 不再满足你对伴侣的期待，你是否依然愿意投入精力去尊重、关心、照顾真实的 Ta，而不是选择离开？",
      "original": "C1",
      "normalizedFrom": "「我对伴侣的期待」改第二人称以统一视角；「关系」按博主 01:37 原话取「关心」义（用户原文疑为笔误）；语义未改",
      "source": "母题 · 01:37",
      "hint": "想定的是 Ta 让你失望之后的事，不是现在还有新鲜感的时候。"
    },
    {
      "id": "I2", "dim": "intimacy", "role": "paraphrase",
      "text": "你是否觉得自己真的了解 Ta 是个什么样的人，并且在 Ta 面前能放松地做自己，不用端着、不用装？",
      "hint": "一半是「了解 Ta」，一半是「敢做自己」，两头都算才答是。"
    },
    {
      "id": "P2", "dim": "passion", "role": "paraphrase",
      "text": "想到和 Ta 有身体上的亲近，你是否有明确的心动和冲动，而不只是「对方愿意的话我也不排斥」？",
      "hint": "「不排斥」不是渴望，这题故意把两者分开。"
    },
    {
      "id": "C2", "dim": "commitment", "role": "paraphrase",
      "text": "做长远打算的时候 —— 城市、生活方式、关系的形态 —— 你会不会自然地把 Ta 算进未来，并且愿意为此约束自己的其他可能？",
      "hint": "不是你嘴上说不说「一辈子」，是你实际做决定时 Ta 在不在里面。"
    },
    {
      "id": "I3", "dim": "intimacy", "role": "paraphrase",
      "text": "当 Ta 过得不好，或者你们很久没见面，你是否会打心底牵挂 Ta，把 Ta 的开心看得和你自己的开心一样重？",
      "hint": "牵挂是「不联系也会想」，不是「联系了才回应」。"
    },
    {
      "id": "P3", "dim": "passion", "role": "paraphrase",
      "text": "Ta 的动静会不会不经意牵动你的情绪 —— 见到 Ta 之前会紧张或期待，看到 Ta 和别人亲近会发酸，分开一段时间会想 Ta？",
      "hint": "问的是不受控的那部分，不是你礼貌性的反应。"
    },
    {
      "id": "C3", "dim": "commitment", "role": "paraphrase",
      "text": "就算这段关系长期平淡、辛苦、没有新鲜感，你是否仍愿意主动去维系它，而不是等 Ta 先放弃？",
      "hint": "关键词是「主动」和「先」。谁在等谁先退出，是很实在的信号。"
    }
  ]
};
