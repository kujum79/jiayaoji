(function() {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();

  // --- 图1：四组家庭7天每餐计划菜品数量 ---
  var chart1 = echarts.init(document.getElementById('chart-dishes'), null, { renderer: 'svg' });
  var dates = ['10-19','10-20','10-21','10-22','10-23','10-24','10-25'];
  var meals = ['早餐','午餐','晚餐'];
  // 数据：[家庭][日期][餐] 计划菜品数
  var famData = {
    '三仁佳欢': [[3,4,4],[3,1,6],[2,4,4],[3,4,4],[1,4,3],[3,4,4],[3,4,4]],
    '非英中义': [[3,4,4],[3,4,0],[3,4,4],[3,4,4],[3,5,5],[3,4,4],[3,4,4]],
    '铁岭之家': [[4,4,4],[4,4,2],[4,4,4],[4,4,4],[4,4,4],[4,5,4],[4,4,4]],
    '小高之家': [[3,4,4],[3,4,0],[3,4,4],[3,4,4],[3,4,3],[3,4,3],[3,4,3]]
  };
  var series = [];
  var fidx = 0;
  Object.keys(famData).forEach(function(fname) {
    var data = famData[fname];
    [0,1,2].forEach(function(mi) {
      var arr = data.map(function(d) { return d[mi]; });
      series.push({
        name: fname + ' ' + meals[mi],
        type: 'bar',
        stack: 's' + fidx,
        data: arr,
        itemStyle: { color: fidx % 2 === 0 ? accent : accent2, opacity: 0.4 + mi * 0.2 }
      });
    });
    fidx++;
  });

  chart1.setOption({
    animation: false,
    tooltip: { trigger: 'axis', appendToBody: true },
    legend: { data: Object.keys(famData).flatMap(function(f){return meals.map(function(m){return f+' '+m;})}), textStyle: { color: muted, fontSize: 10 }, type: 'scroll' },
    grid: { left: 50, right: 20, top: 60, bottom: 40 },
    xAxis: { type: 'category', data: dates, axisLabel: { color: muted } },
    yAxis: { type: 'value', name: '菜品数', axisLabel: { color: muted }, splitLine: { lineStyle: { color: rule } } },
    series: series
  });
  window.addEventListener('resize', function() { chart1.resize(); });

  // --- 图2：铁岭6人份用量系数实证 ---
  var chart2 = echarts.init(document.getElementById('chart-portion'), null, { renderer: 'svg' });
  chart2.setOption({
    animation: false,
    tooltip: { trigger: 'axis', appendToBody: true },
    legend: { data: ['标准成人份', '体力劳动者(×1.2)', '儿童(×0.5)'], textStyle: { color: muted } },
    grid: { left: 60, right: 30, top: 50, bottom: 40 },
    xAxis: { type: 'category', data: ['五花肉(高蛋白)', '大白菜(蔬菜)', '生姜(调料)'], axisLabel: { color: muted } },
    yAxis: { type: 'value', name: '用量(g)', axisLabel: { color: muted }, splitLine: { lineStyle: { color: rule } } },
    series: [
      { name: '标准成人份', type: 'bar', data: [80, 150, 5], itemStyle: { color: muted } },
      { name: '体力劳动者(×1.2)', type: 'bar', data: [96, 150, 5], itemStyle: { color: accent } },
      { name: '儿童(×0.5)', type: 'bar', data: [40, 75, 2.5], itemStyle: { color: accent2 } }
    ]
  });
  window.addEventListener('resize', function() { chart2.resize(); });
})();
