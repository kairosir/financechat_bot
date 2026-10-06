const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); tg.setHeaderColor?.('#0f0a19'); tg.setBackgroundColor?.('#0f0a19'); }

const token = new URLSearchParams(location.search).get('t') || '';
const initData = tg?.initData || '';
const authHeaders = () => ({
  ...(initData ? {'X-Telegram-Init-Data': initData} : {}),
  ...(token ? {'Authorization': `Bearer ${token}`} : {}),
});
const $ = (id) => document.getElementById(id);
let activeKind = 'expense';
let activePeriod = 'month';
let customStart = '';
let customEnd = '';

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {...authHeaders(), ...(options.body ? {'Content-Type':'application/json'} : {}), ...(options.headers || {})},
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || 'Не удалось выполнить запрос');
  return data;
}

function formatDate(value) {
  const date = new Date(value);
  return new Intl.DateTimeFormat('ru-RU', {day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit'}).format(date);
}

function renderDashboard(data) {
  const firstCurrency = data.currency_totals?.[0] || {balance:data.balance,income:data.income,expense:data.expense,currency:'KZT'};
  $('balance').textContent = firstCurrency.balance;
  $('income').textContent = `+${firstCurrency.income}`;
  $('expense').textContent = `−${firstCurrency.expense}`;
  $('periodLabel').textContent = data.period_label.toLowerCase();
  $('historyTitle').textContent = data.period_label;
  $('currencyTotals').innerHTML = (data.currency_totals || []).map(item => `<div class="currency-total"><strong>${escapeHtml(item.balance)}</strong><small>${escapeHtml(item.currency)} · +${escapeHtml(item.income)} / −${escapeHtml(item.expense)}</small></div>`).join('');
  $('accounts').innerHTML = data.accounts.length ? data.accounts.map(item => `
    <article class="account-card"><span>${item.account_type === 'deposit' ? '🏦' : '💳'} ${escapeHtml(item.name)}</span><strong>${escapeHtml(item.balance)}</strong><small>${item.account_type === 'deposit' ? `Депозит${item.interest_rate ? ` · ${escapeHtml(item.interest_rate)} годовых` : ''}` : 'Карта'} · ${escapeHtml(item.currency)}</small></article>`).join('') : '<div class="empty">Счета пока не созданы</div>';
  const max = Math.max(...data.top_categories.map(x => Number(x.amount_minor) || 0), 1);
  $('topCategories').innerHTML = data.top_categories.length ? data.top_categories.map(item => {
    const value = Number(item.amount_minor) || 0;
    return `<div class="chart-row"><div class="chart-meta"><span>${escapeHtml(item.label)}</span><span>${escapeHtml(item.amount)}</span></div><div class="chart-track"><div class="chart-fill" style="width:${Math.max(7,value/max*100)}%"></div></div></div>`;
  }).join('') : '<div class="empty">Расходов в этом месяце ещё нет</div>';
  $('history').innerHTML = data.history.length ? data.history.map(item => `
    <article class="history-item">
      <div class="history-icon">${escapeHtml(item.category.split(' ')[0])}</div>
      <div class="history-copy"><strong>${escapeHtml(item.category)}</strong><small>${escapeHtml(item.account)} · ${formatDate(item.created_at)}${item.note ? ` · ${escapeHtml(item.note)}` : ''}</small></div>
      <div class="history-amount ${item.kind}">${item.kind === 'income' ? '+' : '−'}${escapeHtml(item.amount)}</div>
    </article>`).join('') : '<div class="empty">Добавьте первую операцию — она появится здесь</div>';
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
}

async function refresh() {
  try {
    const params = new URLSearchParams({period:activePeriod});
    if (activePeriod === 'custom') { params.set('start', customStart); params.set('end', customEnd); }
    const data = await api(`/api/dashboard?${params}`);
    renderDashboard(data);
    $('loading').classList.add('hidden'); $('authError').classList.add('hidden'); $('dashboard').classList.remove('hidden'); $('addTransaction').classList.remove('hidden');
    document.querySelector('.status-pill').classList.add('ready'); $('connectionLabel').textContent = 'Всё сохранено';
  } catch (error) {
    $('loading').classList.add('hidden'); $('authError').classList.remove('hidden'); $('connectionLabel').textContent = 'Нужен Telegram';
  }
}

async function loadOptions() {
  const data = await api(`/api/options?kind=${activeKind}`);
  $('categorySelect').innerHTML = '<option value="">Выберите категорию</option>' + data.categories.map(x => `<option value="${escapeHtml(x.value)}">${escapeHtml(x.label)}</option>`).join('');
  $('accountSelect').innerHTML = '<option value="">Выберите счёт</option>' + data.accounts.map(x => `<option value="${x.id}" data-currency="${escapeHtml(x.currency)}">${escapeHtml(x.name)} · ${escapeHtml(x.balance)}</option>`).join('');
}

function setDrawer(open) {
  $('transactionDrawer').classList.toggle('open', open); $('transactionDrawer').setAttribute('aria-hidden', String(!open)); $('drawerBackdrop').classList.toggle('hidden', !open);
  if (open) { loadOptions().catch(showFormError); setTimeout(() => $('amountInput').focus(), 280); }
}

function showFormError(error) { $('formStatus').textContent = error.message || String(error); tg?.HapticFeedback?.notificationOccurred('error'); }

$('addTransaction').onclick = () => setDrawer(true); $('closeDrawer').onclick = () => setDrawer(false); $('drawerBackdrop').onclick = () => setDrawer(false);
document.querySelectorAll('[data-kind]').forEach(button => button.onclick = () => {
  activeKind = button.dataset.kind; document.querySelectorAll('[data-kind]').forEach(x => x.classList.toggle('active', x === button)); loadOptions().catch(showFormError);
});

$('transactionForm').onsubmit = async (event) => {
  event.preventDefault(); const button = $('saveTransaction'); button.disabled = true; $('formStatus').textContent = 'Сохраняем…';
  try {
    await api('/api/transactions', {method:'POST', body:JSON.stringify({kind:activeKind, amount:$('amountInput').value, category:$('categorySelect').value, account_id:$('accountSelect').value, note:$('noteInput').value})});
    tg?.HapticFeedback?.notificationOccurred('success'); $('transactionForm').reset(); $('formStatus').textContent = '✓ Операция сохранена'; await refresh(); setTimeout(() => setDrawer(false), 650);
  } catch (error) { showFormError(error); } finally { button.disabled = false; }
};

function openDialog(mode) {
  const account = mode === 'account'; $('dialogTitle').textContent = account ? 'Новый счёт' : 'Новая категория';
  ['dialogBalanceLabel','dialogTypeLabel','dialogCurrencyLabel'].forEach(id => $(id).classList.toggle('hidden', !account));
  $('dialogInterestLabel').classList.add('hidden'); $('dialogName').value = ''; $('dialogBalance').value = '0'; $('dialogInterest').value = ''; $('dialogType').value = 'card'; $('dialogCurrency').value = 'KZT'; $('dialogStatus').textContent = ''; $('smallDialog').dataset.mode = mode; $('smallDialog').showModal(); setTimeout(() => $('dialogName').focus(), 80);
}
$('newAccount').onclick = () => openDialog('account'); $('newCategory').onclick = () => openDialog('category');
$('closeSmallDialog').onclick = () => $('smallDialog').close();
$('dialogType').onchange = () => $('dialogInterestLabel').classList.toggle('hidden', $('dialogType').value !== 'deposit');
$('smallDialogForm').onsubmit = async (event) => {
  event.preventDefault(); const account = $('smallDialog').dataset.mode === 'account'; $('dialogStatus').textContent = 'Сохраняем…';
  try {
    await api(account ? '/api/accounts' : '/api/categories', {method:'POST', body:JSON.stringify(account ? {name:$('dialogName').value,balance:$('dialogBalance').value,account_type:$('dialogType').value,currency:$('dialogCurrency').value,interest_rate:$('dialogInterest').value} : {name:$('dialogName').value,kind:activeKind})});
    $('smallDialog').close(); tg?.HapticFeedback?.notificationOccurred('success'); await refresh(); if (!account) await loadOptions();
  } catch (error) { event.preventDefault(); $('dialogStatus').textContent = error.message; }
};

document.querySelectorAll('[data-period]').forEach(button => button.onclick = async () => {
  const period = button.dataset.period;
  if (period === 'custom') { $('customPeriod').classList.remove('hidden'); return; }
  activePeriod = period; $('customPeriod').classList.add('hidden'); document.querySelectorAll('[data-period]').forEach(x => x.classList.toggle('active', x === button)); await refresh();
});
$('applyPeriod').onclick = async () => {
  customStart = $('periodStart').value; customEnd = $('periodEnd').value;
  if (!customStart || !customEnd) { tg?.showAlert?.('Выберите обе даты'); return; }
  activePeriod = 'custom'; document.querySelectorAll('[data-period]').forEach(x => x.classList.toggle('active', x.dataset.period === 'custom'));
  try { await refresh(); } catch (error) { tg?.showAlert?.(error.message); }
};

async function openUpdates() {
  $('updatesDialog').showModal();
  try {
    const data = await api('/api/whats-new');
    $('updatesList').innerHTML = data.releases.map(release => `<article class="release-card"><h4>Версия ${escapeHtml(release.version)}</h4><time>${escapeHtml(release.date)}</time><p>${escapeHtml(release.title)}</p><ul>${release.items.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></article>`).join('');
  } catch (error) { $('updatesList').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`; }
}
$('whatsNewButton').onclick = openUpdates;
$('closeUpdates').onclick = () => $('updatesDialog').close();

refresh();
