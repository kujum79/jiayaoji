// 渲染四组家庭7天数据
(function() {
  if (!window.familyData) return;
  var container = document.getElementById('familyDataContainer');
  if (!container) return;

  var fidOrder = ['BHDVWN36', 'NJQSFVA5', 'KWL47P39', 'V2C8WRFR'];
  var dates = ['2026-10-19','2026-10-20','2026-10-21','2026-10-22','2026-10-23','2026-10-24','2026-10-25'];
  var meals = [
    {key:'breakfast', label:'早'},
    {key:'lunch', label:'午'},
    {key:'dinner', label:'晚'}
  ];

  var typeColor = {
    '外食': 'background:rgba(220,38,38,0.08);border-left-color:#dc2626;',
    '分餐': 'background:rgba(217,119,6,0.08);border-left-color:#d97706;',
    '补录': 'background:rgba(138,133,126,0.06);border-left-color:#8a857e;',
    '真实': 'background:rgba(22,163,74,0.08);border-left-color:#16a34a;',
    '无': 'background:rgba(255,255,255,0.5);border-left-color:transparent;'
  };

  fidOrder.forEach(function(fid) {
    var fam = window.familyData[fid];
    if (!fam) return;
    var block = document.createElement('div');
    block.className = 'family-block';
    var html = '<div class="family-header"><h3>' + fam.name + '</h3><span class="family-badge">' + fid + '</span></div>';
    html += '<div class="meal-grid">';
    dates.forEach(function(d) {
      html += '<div class="day-col"><div class="date">' + d.slice(5) + '</div>';
      meals.forEach(function(m) {
        var md = (fam.days[d] || {})[m.key] || {};
        var dishes = md.actual_dishes && md.actual_dishes.length ? md.actual_dishes : (md.plan_dishes || []);
        var type = md.actual_type || '无';
        var style = typeColor[type] || typeColor['无'];
        html += '<div class="meal"><div class="meal-label">' + m.label + ' <span style="color:var(--muted)">[' + type + ']</span></div>';
        dishes.forEach(function(dish) {
          html += '<div class="dish" style="' + style + 'border-left:3px solid;">' + dish + '</div>';
        });
        if (!dishes.length) html += '<div class="dish" style="color:var(--muted);border-left:3px solid transparent;background:transparent;">—</div>';
        if (md.actual_type === '外食') {
          html += '<div class="meal-extra out">🍽️ ' + (md.restaurant || '外出就餐') +
                  (md.people ? ' · ' + md.people + '人' : '') +
                  (md.total ? ' · ¥' + md.total : '') + '</div>';
        }
        if (md.actual_type === '分餐' && md.locationSummary) {
          html += '<div class="meal-extra split">🔀 ' + md.locationSummary + '</div>';
        }
        html += '</div>';
      });
      html += '</div>';
    });
    html += '</div>';
    block.innerHTML = html;
    container.appendChild(block);
  });
})();
