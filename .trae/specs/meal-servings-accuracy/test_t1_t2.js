(function() {
  var w = window;
  var root = document.documentElement;
  root.style.overflowX = 'hidden';
  var bodyEl = document.body;
  bodyEl.style.minWidth = '390px';
  bodyEl.style.maxWidth = '390px';
  bodyEl.style.marginLeft = 'auto';
  bodyEl.style.marginRight = 'auto';
  var meta = document.querySelector('meta[name=viewport]');
  if (!meta) { meta = document.createElement('meta'); meta.name='viewport'; document.head.appendChild(meta); }
  meta.setAttribute('content','width=390,initial-scale=1,maximum-scale=1,user-scalable=no');

  function pad(n){return n<10?'0'+n:''+n;}
  function dk(d){return d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate());}
  var today = new Date(); today.setHours(0,0,0,0);
  var tmr = new Date(today); tmr.setDate(today.getDate()+1);
  var T0 = dk(today);
  var T1 = dk(tmr);

  var tomatoDish = {
    name: '番茄炒蛋',
    ingredients: [
      { name: '番茄', quantity: 100, unit: 'g', category: '蔬菜' },
      { name: '鸡蛋', quantity: 50, unit: 'g', category: '肉蛋奶' }
    ]
  };
  var cal = {};
  cal[T0] = {
    lunch: { plan: { dishes: [tomatoDish], servings: 3, confirmed:true, state:'confirmed', confirmTime:new Date().toISOString() } },
    dinner:{ plan: { dishes: [tomatoDish], servings: 2, confirmed:true, state:'confirmed', confirmTime:new Date().toISOString() } },
    breakfast: { plan: { dishes: [] } }
  };
  cal[T1] = {
    lunch: { plan: { dishes: [tomatoDish], servings: 4, confirmed:true, state:'confirmed', confirmTime:new Date().toISOString() } },
    dinner:{ plan: { dishes: [tomatoDish], servings: 2, confirmed:true, state:'confirmed', confirmTime:new Date().toISOString() } },
    breakfast: { plan: { dishes: [] } }
  };
  try { localStorage.setItem('dietCalendar', JSON.stringify(cal)); } catch(e) {}
  if (typeof saveDietCalendar === 'function') { try{ saveDietCalendar(cal); }catch(_){} }

  var out = {};
  out.T0 = T0;
  out.T1 = T1;

  var origGetMealData = w.getMealData;
  try {
    w.getMealData = function(){ return { plan: {} }; };
    var g = w.getMealServings('2099-01-01', 'lunch');
    var exp = 1;
    try { var cs = Number(w.getCurrentServings()); if (!isNaN(cs) && cs>=1) exp = Math.floor(cs); } catch(_) {}
    out.T1_1 = { got:g, expected:exp, pass: (g===exp) && (g>=1) };
  } catch(e) { out.T1_1 = { error:String(e) }; }
  finally { if (origGetMealData) w.getMealData = origGetMealData; }

  try {
    w.getMealData = function(){ return { plan: { servings:3 } }; };
    var v2 = w.getMealServings('x','y');
    out.T1_2 = { got:v2, expected:3, pass: v2===3 };
  } catch(e) { out.T1_2 = { error:String(e) }; }
  finally { if (origGetMealData) w.getMealData = origGetMealData; }

  try {
    w.getMealData = function(){ return { plan: { servings:0, diningMembers:[{},{}] } }; };
    var v3 = w.getMealServings('x','y');
    out.T1_3 = { got:v3, expected:2, pass: v3===2 };
  } catch(e) { out.T1_3 = { error:String(e) }; }
  finally { if (origGetMealData) w.getMealData = origGetMealData; }

  try {
    var info = w.countConfirmedPlanDays(today);
    var daysList = (info.daysList || []).slice(0,2);
    var res = w.computeIngredientNeeds(daysList, 9, 2);
    var tomatoRow = null, eggRow = null, anyNote = '';
    Object.keys(res.ingredientsMap||{}).forEach(function(k){
      var r = res.ingredientsMap[k];
      if (r.name === '番茄') tomatoRow = r;
      if (r.name === '鸡蛋') eggRow = r;
      if (!anyNote && r.displayNote) anyNote = r.displayNote;
    });
    var note = (tomatoRow && tomatoRow.displayNote) ? tomatoRow.displayNote : anyNote;
    var T2_1 = { got: tomatoRow ? tomatoRow.totalQty : null, expected: 1100, pass: tomatoRow && tomatoRow.totalQty === 1100 };
    var T2_2 = { got: eggRow ? eggRow.totalQty : null, expected: 550, pass: eggRow && Math.abs(eggRow.totalQty - 550) < 0.001 };
    var badRx = /\u00d7\d+\u4eba\u00d7\d+\u5929/;
    var goodRx = /\u00d7\d+\u4eba\u00b7\u9910/;
    var T2_3 = { note: note, badPattern: badRx.test(note), goodPattern: goodRx.test(note), pass: !badRx.test(note) && goodRx.test(note) };
    out.T2 = { T2_1:T2_1, T2_2:T2_2, T2_3:T2_3, servingsField: res.servings, mapKeys: Object.keys(res.ingredientsMap||{}).length };
  } catch(e) { out.T2 = { error:String(e) }; }

  // T4.1: switch to fridge tab and look for summary
  try {
    if (typeof switchTab === 'function') switchTab('fridge');
    out.switched = typeof switchTab === 'function' ? 'ok' : 'no-switchTab';
  } catch(e) { out.swErr = String(e); }

  return JSON.stringify(out);
})()
