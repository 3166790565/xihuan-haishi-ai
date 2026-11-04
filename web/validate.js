/*
 * 数据契约自检 —— 页面加载时跑，不通过就在页面上明示，而不是让人猜为什么渲染不对。
 *
 * 存在的理由：本项目的两个约束都靠数据文件维持，改文案的非程序员（包括未来的我）
 * 很容易不小心破坏，而破坏后的表现是「悄悄显示错内容」而不是报错：
 *   1. 9 题、每维度 3 题、题目 dim 合法 —— 否则维度分算错。
 *   2. 三道母题（role:"anchor"）必须与 QUIZ_DATA.originals 语义同源 ——
 *      用户明确要求母题原意不能被改写跑偏，这不能靠自觉。
 *   3. types 的 8 个真类型 + sprout 必须齐全，且每个有 name/headline/body ——
 *      缺一个就会在某种答案组合下开天窗。
 *
 * 用 window.QUIZ_DATA / window.QUIZ_TEXTS（.js 数据文件），不 fetch，因此 file:// 下同样可用。
 */
(function (global) {
  "use strict";

  var REQUIRED_TYPE_KEYS = [
    "nonlove", "liking", "infatuation", "empty",
    "romantic", "companionate", "fatuous", "consummate", "sprout"
  ];
  var REQUIRED_DIMS = ["intimacy", "passion", "commitment"];

  function isNonEmptyString(v) {
    return typeof v === "string" && v.trim().length > 0;
  }

  /**
   * 返回 { errors: [...], warnings: [...], filled: {...} }
   * errors  = 会让判定或渲染出错的结构性问题
   * warnings= 可上线但需要知道的事（典型：文案还没填）
   */
  function check(data, texts) {
    var errors = [];
    var warnings = [];
    var filled = { types: 0, typesTotal: REQUIRED_TYPE_KEYS.length, videoQuotes: 0 };

    if (!data || !data.questions) {
      return { errors: ["QUIZ_DATA 缺失或没有 questions"], warnings: warnings, filled: filled };
    }

    var qs = data.questions;
    if (qs.length !== 9) {
      errors.push("题目数应为 9，实际 " + qs.length);
    }

    var byDim = {};
    var ids = {};
    REQUIRED_DIMS.forEach(function (d) { byDim[d] = []; });

    qs.forEach(function (q, i) {
      if (!isNonEmptyString(q.id)) errors.push("第 " + (i + 1) + " 题缺 id");
      if (ids[q.id]) errors.push("题目 id 重复：" + q.id);
      ids[q.id] = true;
      if (!byDim.hasOwnProperty(q.dim)) {
        errors.push("题 " + q.id + " 的 dim 非法：" + q.dim);
      } else {
        byDim[q.dim].push(q.id);
      }
      if (!isNonEmptyString(q.text)) errors.push("题 " + q.id + " 缺 text");
      if (q.role !== "anchor" && q.role !== "paraphrase") {
        errors.push("题 " + q.id + " 的 role 非法：" + q.role);
      }
    });

    REQUIRED_DIMS.forEach(function (d) {
      if (byDim[d].length !== 3) {
        errors.push("维度 " + d + " 应有 3 题，实际 " + byDim[d].length + "（" + byDim[d].join(",") + "）");
      }
    });

    // 母题保护：每个维度必须恰好一道 anchor，且带 original 指向 originals。
    REQUIRED_DIMS.forEach(function (d) {
      var anchors = byDim[d].filter(function (id) {
        return qs.some(function (q) { return q.id === id && q.role === "anchor"; });
      });
      if (anchors.length !== 1) {
        errors.push("维度 " + d + " 应有恰好 1 道母题(anchor)，实际 " + anchors.length);
      }
    });

    var originals = data.originals || {};
    qs.filter(function (q) { return q.role === "anchor"; }).forEach(function (q) {
      var src = originals[q.original];
      if (!isNonEmptyString(src)) {
        errors.push("母题 " + q.id + " 的 original 键 " + q.original + " 在 originals 里不存在");
        return;
      }
      // 逐字比对：规范化过的字（「关系」→「关心」、空格、破折号）会在这里暴露，
      // 由 questions.js 里的 normalizedFrom 字段声明，人工确认是否可接受。
      if (q.text.replace(/\s+/g, "") !== src.replace(/\s+/g, "")) {
        if (!isNonEmptyString(q.normalizedFrom)) {
          errors.push("母题 " + q.id + " 与 original 原文不一致，且未声明 normalizedFrom 说明改动理由");
        } else {
          warnings.push("母题 " + q.id + " 相对原文有改写（已声明理由：" + q.normalizedFrom + "）");
        }
      }
    });

    // 规则字段
    var rules = data.rules || {};
    if (rules.presentThreshold !== 2) {
      warnings.push("presentThreshold 当前是 " + rules.presentThreshold + "（判定表按 2 人工核对过）");
    }

    if (!texts) {
      errors.push("QUIZ_TEXTS 缺失");
      return { errors: errors, warnings: warnings, filled: filled };
    }

    var types = texts.types || {};
    REQUIRED_TYPE_KEYS.forEach(function (key) {
      var t = types[key];
      if (!t) {
        errors.push("types." + key + " 缺失（该答案组合下会开天窗）");
        return;
      }
      ["name", "headline"].forEach(function (f) {
        if (!isNonEmptyString(t[f])) errors.push("types." + key + "." + f + " 为空");
      });
      if (!Array.isArray(t.body) || t.body.length === 0) {
        warnings.push("types." + key + ".body 为空（该区块不渲染，属正常降级）");
      } else {
        filled.types++;
        t.body.forEach(function (p, i) {
          if (!isNonEmptyString(p)) errors.push("types." + key + ".body[" + i + "] 不是字符串或为空");
        });
      }
      if (isNonEmptyString(t.quote) && !isNonEmptyString(t.quoteTime)) {
        warnings.push("types." + key + " 有 quote 但缺 quoteTime（引用不可回溯）");
      }
      if (isNonEmptyString(t.quoteTime) && !/^\d{2}:\d{2}/.test(t.quoteTime)) {
        warnings.push("types." + key + ".quoteTime 格式不像时间码：" + t.quoteTime);
      }
    });

    var vq = (texts.video && texts.video.paragraphs) || [];
    filled.videoQuotes = vq.length;
    if (vq.length === 0) warnings.push("video.paragraphs 为空：视频出处区整块不渲染");

    REQUIRED_DIMS.forEach(function (d) {
      var dd = (texts.dims || {})[d];
      if (!dd || !isNonEmptyString(dd.name)) errors.push("dims." + d + ".name 缺失");
    });

    ["like", "love", "none", "mixed"].forEach(function (v) {
      var item = (texts.verdicts || {})[v];
      if (!item || !isNonEmptyString(item.headline)) {
        errors.push("verdicts." + v + ".headline 缺失（结果页主结论会空）");
      }
    });

    return { errors: errors, warnings: warnings, filled: filled };
  }

  var api = { check: check, REQUIRED_TYPE_KEYS: REQUIRED_TYPE_KEYS, REQUIRED_DIMS: REQUIRED_DIMS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.QUIZ_VALIDATE = api;
})(typeof window !== "undefined" ? window : globalThis);
