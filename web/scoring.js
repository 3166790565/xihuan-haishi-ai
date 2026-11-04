/*
 * 判定核心 —— 纯函数，不碰 DOM、不含任何中文句子。
 *
 * 后端 app/scoring.py 是这套规则的 Python 同构实现，
 * 两边共用 tests/golden-vectors.json 做穷举回归，必须全绿且结论一致：
 * 前端负责即时出结果，后端负责发信前重新计算（不信任客户端提交的结论）。
 *
 * 规则来源：斯滕伯格爱情三角理论。
 *   每个维度 3 题、是=1 否=0，得分 >= presentThreshold(2) 认定该成分「在场」。
 *   三个成分的在场组合映射到八种爱的类型。
 *   二元选项信息量有限，所以额外产出临界维度与「关键题」如实呈现，而不是硬猜。
 */
(function (global) {
  "use strict";

  var DIM_ORDER = ["intimacy", "passion", "commitment"];

  // 下标 = I*4 + P*2 + C，值 = 类型 key。
  // 别按「亲密/激情/承诺」的阅读顺序去数下标：下标 1 是「只有承诺」，
  // 下标 4 才是「只有亲密」。这一格写反过，黄金向量的 V3/V5 专门盯它。
  var TYPE_BY_BITS = [
    "nonlove",        // 0 (I-P C-)  三者都不在场
    "empty",          // 1 (I- P- C+) 只有承诺 → 空洞之爱
    "infatuation",    // 2 (I- P+ C-) 只有激情 → 迷恋
    "fatuous",        // 3 (I- P+ C+) 激情+承诺，缺亲密 → 愚蠢之爱
    "liking",         // 4 (I+ P- C-) 只有亲密 → 喜欢 ★本测试的核心判别
    "companionate",   // 5 (I+ P- C+) 亲密+承诺 → 相伴之爱
    "romantic",       // 6 (I+ P+ C-) 亲密+激情 → 浪漫之爱
    "consummate"      // 7 (I+ P+ C+) 三者齐备 → 圆满之爱
  ];

  var TYPE_ORDER = [
    "nonlove", "liking", "infatuation", "empty",
    "romantic", "companionate", "fatuous", "consummate", "sprout"
  ];

  // verdict 桶写死在规则里，措辞永远取自 interpretations.js，逻辑层不出文案。
  //
  // 分桶依据是 博客.mp4 博主的原话（02:41）：
  //   「除此之外，接下来的所有答案就都是爱了……只是有各种各样不同的形式。」
  // 所以只有「否否否」不算情感连接、「是否否」是喜欢但不爱，其余六种一律归 love。
  // 不要按「缺亲密就不算爱」的学术中立版改这里 —— 那会和界面文案自相矛盾。
  //
  // key 名沿用学术通译（fatuous / companionate / consummate），
  // 界面上的中文名用博主的叫法（冲动之爱 / 伴侣之爱 / 完满之爱），取自数据层。
  var TYPE_META = {
    nonlove:      { bits: [0, 0, 0], verdict: "none" },
    liking:       { bits: [1, 0, 0], verdict: "like" },
    infatuation:  { bits: [0, 1, 0], verdict: "love" },
    empty:        { bits: [0, 0, 1], verdict: "love" },
    romantic:     { bits: [1, 1, 0], verdict: "love" },
    companionate: { bits: [1, 0, 1], verdict: "love" },
    fatuous:      { bits: [0, 1, 1], verdict: "love" },
    consummate:   { bits: [1, 1, 1], verdict: "love" },
    // 「萌芽/混合期」不是理论类型，是 9 题制才出现的答案向量
    // （三成分各 1 分，没有任何成分达到在场阈值）。不硬猜，如实标难判。
    sprout:       { bits: null,       verdict: "mixed" }
  };

  function normalizeRules(data) {
    var r = (data && data.rules) || {};
    return {
      threshold: typeof r.presentThreshold === "number" ? r.presentThreshold : 2,
      perDim: typeof r.perDim === "number" ? r.perDim : 3
    };
  }

  /*
   * answers: { 题目id: 0|1 }
   * 缺题或非法值抛错 —— 后端拿这个做请求合法性校验，不能静默当 0。
   */
  function validateAnswers(answers, data) {
    var qs = (data && data.questions) || [];
    if (!answers || typeof answers !== "object") {
      throw new Error("answers must be an object");
    }
    var clean = {};
    for (var i = 0; i < qs.length; i++) {
      var q = qs[i];
      if (!Object.prototype.hasOwnProperty.call(answers, q.id)) {
        throw new Error("missing answer: " + q.id);
      }
      var v = Number(answers[q.id]);
      if (v !== 0 && v !== 1) {
        throw new Error("answer must be 0 or 1: " + q.id);
      }
      clean[q.id] = v;
    }
    return clean;
  }

  function dimScores(answers, data) {
    var scores = { intimacy: 0, passion: 0, commitment: 0 };
    var qs = data.questions;
    for (var i = 0; i < qs.length; i++) {
      var q = qs[i];
      if (scores[q.dim] === undefined) {
        throw new Error("unknown dim: " + q.dim);
      }
      scores[q.dim] += answers[q.id] || 0;
    }
    return scores;
  }

  function classify(scores, rules) {
    var bits = DIM_ORDER.map(function (d) {
      return scores[d] >= rules.threshold ? 1 : 0;
    });
    // 三维都刚好一分：什么成分都没成形，但也不是「无感」。
    // 这种答案向量判成无爱是错的，单独归为萌芽/混合期。
    var allOne = DIM_ORDER.every(function (d) { return scores[d] === 1; });
    var key = allOne ? "sprout" : TYPE_BY_BITS[bits[0] * 4 + bits[1] * 2 + bits[2]];
    var present = {};
    for (var i = 0; i < DIM_ORDER.length; i++) present[DIM_ORDER[i]] = bits[i] === 1;
    return { bits: bits, key: key, present: present };
  }

  /*
   * 临界维度：得分刚好在阈值线上下 1 题以内（threshold-1 或 threshold）。
   * 这些维度一旦被翻动，结论就可能整体改变，必须如实告诉用户。
   */
  function borderlineDims(scores, rules) {
    return DIM_ORDER.filter(function (d) {
      return scores[d] === rules.threshold || scores[d] === rules.threshold - 1;
    });
  }

  /*
   * 关键题 —— 定义就一句话：把这道题的是/否翻过来重算，结论桶（like/love/none/mixed）
   * 会变，这道题就是关键题。「你的结论取决于这几题」必须与这个定义严格对应，
   * 所以这里对全部 9 题逐一尝试、不做任何剪枝 —— 剪枝省下的运行时间与可验证性相比不值钱。
   *
   * 唯一的例外是 sprout（萌芽/混合期）：它是全局奇点，三维各 1 分，翻任意一题都会跳进
   * 某个「爱」的桶，于是 9 题全部命中 —— 那不是「你的结论取决于这几题」，那是「取决于全部题」，
   * 等于没说。sprout 改由结果页的「逐题回顾」呈现（明确列出你答是的只有哪三题）。
   */
  function keyQuestions(answers, data, rules) {
    var base = classify(dimScores(answers, data), rules);
    if (base.key === "sprout") return [];

    var baseVerdict = TYPE_META[base.key].verdict;
    var hits = [];
    var qs = data.questions;
    for (var i = 0; i < qs.length; i++) {
      var q = qs[i];
      var flipped = {};
      for (var k in answers) flipped[k] = answers[k];
      flipped[q.id] = answers[q.id] === 1 ? 0 : 1;
      var alt = classify(dimScores(flipped, data), rules);
      if (TYPE_META[alt.key].verdict !== baseVerdict) hits.push(q.id);
    }
    return hits;
  }

  // 展示次序固定 I > C > P。这只是排序 tie-break，不是理论上的优先级 ——
  // 三个成分在斯滕伯格理论里是平等的，代码注释和文案都不许把它读成"亲密更重要"。
  var DISPLAY_TIE_ORDER = ["intimacy", "commitment", "passion"];

  function rankDims(scores) {
    return DISPLAY_TIE_ORDER.slice().sort(function (a, b) {
      if (scores[b] !== scores[a]) return scores[b] - scores[a];
      return DISPLAY_TIE_ORDER.indexOf(a) - DISPLAY_TIE_ORDER.indexOf(b);
    });
  }

  function evaluate(answers, data) {
    var rules = normalizeRules(data);
    var clean = validateAnswers(answers, data);
    var scores = dimScores(clean, data);
    var cls = classify(scores, rules);
    var borderline = borderlineDims(scores, rules);
    var keys = keyQuestions(clean, data, rules);
    return {
      version: 1,
      scores: scores,
      total: scores.intimacy + scores.passion + scores.commitment,
      present: cls.present,
      bits: cls.bits,
      typeKey: cls.key,
      verdict: TYPE_META[cls.key].verdict,
      threshold: rules.threshold,
      borderlineDims: borderline,
      keyQuestionIds: keys,
      confidence: keys.length > 0 ? "low" : "normal",
      dimRank: rankDims(scores)
    };
  }

  var api = {
    DIM_ORDER: DIM_ORDER,
    TYPE_ORDER: TYPE_ORDER,
    TYPE_META: TYPE_META,
    TYPE_BY_BITS: TYPE_BY_BITS,
    evaluate: evaluate,
    validateAnswers: validateAnswers,
    dimScores: dimScores,
    classify: classify,
    keyQuestions: keyQuestions,
    borderlineDims: borderlineDims,
    rankDims: rankDims
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.SCORING = api;
})(typeof window !== "undefined" ? window : globalThis);
