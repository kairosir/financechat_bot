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
  $('balance').textContent = data.balance;
  $('income').textContent = `+${data.income}`;
  $('expense').textContent = `−${data.expense}`;
  $('accounts').innerHTML = data.accounts.length ? data.accounts.map(item => `
    <article class="account-card"><span>${escapeHtml(item.name)}</span><strong>${escapeHtml(item.balance)}</strong></article>`).join('') : '<div class="empty">Счета пока не созданы</div>';
  const max = Math.max(...data.top_categories.map(x => parseFloat(x.amount.replace(/[^\d,.-]/g,'').replace(',','.')) || 0), 1);
  $('topCategories').innerHTML = data.top_categories.length ? data.top_categories.map(item => {
    const value = parseFloat(item.amount.replace(/[^\d,.-]/g,'').replace(',','.')) || 0;
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
    const data = await api('/api/dashboard');
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
  $('accountSelect').innerHTML = '<option value="">Выберите счёт</option>' + data.accounts.map(x => `<option value="${x.id}">${escapeHtml(x.name)} · ${escapeHtml(x.balance)}</option>`).join('');
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
  const account = mode === 'account'; $('dialogTitle').textContent = account ? 'Новый счёт' : 'Новая категория'; $('dialogBalanceLabel').classList.toggle('hidden', !account); $('dialogName').value = ''; $('dialogBalance').value = '0'; $('dialogStatus').textContent = ''; $('smallDialog').dataset.mode = mode; $('smallDialog').showModal(); setTimeout(() => $('dialogName').focus(), 80);
}
$('newAccount').onclick = () => openDialog('account'); $('newCategory').onclick = () => openDialog('category');
$('smallDialogForm').onsubmit = async (event) => {
  event.preventDefault(); const account = $('smallDialog').dataset.mode === 'account'; $('dialogStatus').textContent = 'Сохраняем…';
  try {
    await api(account ? '/api/accounts' : '/api/categories', {method:'POST', body:JSON.stringify(account ? {name:$('dialogName').value,balance:$('dialogBalance').value} : {name:$('dialogName').value,kind:activeKind})});
    $('smallDialog').close(); tg?.HapticFeedback?.notificationOccurred('success'); await refresh(); if (!account) await loadOptions();
  } catch (error) { event.preventDefault(); $('dialogStatus').textContent = error.message; }
};

refresh();
