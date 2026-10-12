'use strict';

const $ = id => document.getElementById(id);
const tg = window.Telegram?.WebApp;
const telegramInitData = tg?.initData || '';
const shortcutToken = new URLSearchParams(location.search).get('t') || '';
const isMiniApp = Boolean(telegramInitData);
const isShortcut = Boolean(shortcutToken);
const currencySymbols = {KZT:'₸', RUB:'₽', USD:'$'};
const accountMeta = {
  card:{icon:'💳', label:'Карта'}, cash:{icon:'💵', label:'Наличные'}, deposit:{icon:'🏦', label:'Депозит'},
};

let authClient = null;
let authSession = null;
let authToken = '';
let authMode = 'signin';
let activeKind = 'expense';
let activePeriod = 'month';
let customStart = '';
let customEnd = '';
let accountType = 'card';
let dashboardData = null;
let telegramLinkToken = '';
let telegramLinkTimer = null;
let toastTimer = null;

const ACTIVITY_KEY = 'purpose_finance_activity_v2';
const LINK_KEY = 'purpose_finance_link_v2';
const INACTIVITY_MS = 3 * 24 * 60 * 60 * 1000;

if (isMiniApp) {
  document.documentElement.classList.add('telegram-mini-app');
  tg.ready();
  tg.expand();
  tg.setHeaderColor?.('#f6f4f9');
  tg.setBackgroundColor?.('#f6f4f9');
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}

function setStatus(id, message = '', type = '') {
  const element = $(id);
  if (!element) return;
  element.textContent = message;
  element.classList.toggle('error', type === 'error');
  element.classList.toggle('success', type === 'success');
}

function toast(message, type = '') {
  clearTimeout(toastTimer);
  const element = $('toast');
  element.textContent = message;
  element.style.background = type === 'error' ? '#9d2940' : '';
  element.classList.add('show');
  toastTimer = setTimeout(() => element.classList.remove('show'), 2800);
}

function friendlyError(error, fallback = 'Что-то пошло не так') {
  const message = String(error?.message || error || '').trim();
  const value = message.toLowerCase();
  if (value.includes('invalid email or password') || value.includes('invalid password')) return 'Неверный email или пароль';
  if (value.includes('already exists') || value.includes('already registered')) return 'Аккаунт с таким email уже существует';
  if (value.includes('password') && (value.includes('short') || value.includes('8'))) return 'Пароль должен содержать не менее 8 символов';
  if (value.includes('too many')) return 'Слишком много попыток. Попробуйте немного позже.';
  if (value.includes('current password')) return 'Текущий пароль указан неверно';
  return message || fallback;
}

function tokenExpiresSoon(token, threshold = 30_000) {
  if (!token || token.split('.').length !== 3) return true;
  try {
    const encoded = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const payload = JSON.parse(atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, '=')));
    return !payload.exp || payload.exp * 1000 - Date.now() <= threshold;
  } catch (_) { return true; }
}

async function refreshAuthToken(attempts = 1) {
  if (!authClient) return false;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const result = await authClient.getSession();
    authSession = result?.data?.user ? result.data : null;
    authToken = '';
    if (!authSession) return false;
    try {
      const tokenResult = await authClient.token();
      authToken = tokenResult?.data?.token || tokenResult?.data?.session?.token || '';
    } catch (_) {}
    authToken ||= authSession?.session?.token || '';
    if (authToken) return true;
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  return false;
}

function requestHeaders() {
  return {
    ...(telegramInitData ? {'X-Telegram-Init-Data':telegramInitData} : {}),
    ...((authToken || shortcutToken) ? {Authorization:`Bearer ${authToken || shortcutToken}`} : {}),
  };
}

async function api(path, options = {}) {
  if (authClient && authSession && tokenExpiresSoon(authToken)) await refreshAuthToken(2);
  const execute = () => fetch(path, {
    ...options,
    headers:{...requestHeaders(), ...(options.body ? {'Content-Type':'application/json'} : {}), ...(options.headers || {})},
  });
  let response = await execute();
  if (response.status === 401 && authClient && authSession && await refreshAuthToken(2)) response = await execute();
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || payload.message || 'Не удалось выполнить запрос');
  return payload;
}

function showOnly(screenId) {
  ['bootScreen','authScreen','miniAppError','appScreen'].forEach(id => $(id).classList.toggle('hidden', id !== screenId));
}

function rememberActivity() {
  if (isMiniApp || isShortcut || !authSession) return;
  localStorage.setItem(ACTIVITY_KEY, String(Date.now()));
}

function sessionInactive() {
  const last = Number(localStorage.getItem(ACTIVITY_KEY) || 0);
  return last > 0 && Date.now() - last >= INACTIVITY_MS;
}

async function signOut({inactive = false} = {}) {
  try { await authClient?.signOut(); } catch (_) {}
  authSession = null;
  authToken = '';
  localStorage.removeItem(ACTIVITY_KEY);
  stopLinkPolling();
  showOnly('authScreen');
  setAuthMode('signin');
  setStatus('authStatus', inactive ? 'Сессия завершена после 3 дней без активности' : 'Вы вышли из аккаунта', inactive ? 'error' : 'success');
}

['pointerdown','keydown','touchstart'].forEach(name => addEventListener(name, rememberActivity, {passive:true}));
setInterval(() => { if (authSession && sessionInactive()) signOut({inactive:true}); }, 60_000);

function accountCard(item) {
  const meta = accountMeta[item.account_type] || accountMeta.card;
  let details = `${meta.label} · ${escapeHtml(item.currency)}`;
  if (item.account_type === 'deposit') {
    details = `Ном. ${escapeHtml(item.nominal_interest_rate || item.interest_rate || '—')} · Эфф. ${escapeHtml(item.effective_interest_rate || '—')}`;
    if (item.daily_interest_enabled) details += ` · ежедневно<br>Сегодня +${escapeHtml(item.interest_accrued_today || '0')} · примерно ${escapeHtml(item.estimated_daily_interest || '0')}/день · расчётная эфф. ${escapeHtml(item.calculated_effective_rate || '—')}`;
  }
  return `<button type="button" class="account-card ${escapeHtml(item.account_type)}" data-edit-account="${item.id}"><div class="account-head"><span>${escapeHtml(item.name)}</span><span class="account-icon">${meta.icon}</span></div><strong>${escapeHtml(item.balance)}</strong><small>${details}</small></button>`;
}

function formatDate(value) {
  const date = new Date(value);
  return new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}).format(date);
}

function historyItem(item) {
  const icon = String(item.category || '•').split(' ')[0];
  const note = item.note ? ` · ${escapeHtml(item.note)}` : '';
  return `<article class="history-item"><div class="history-icon">${escapeHtml(icon)}</div><div class="history-copy"><strong>${escapeHtml(item.category)}</strong><small>${escapeHtml(item.account)} · ${formatDate(item.created_at)}${note}</small></div><div class="history-amount ${escapeHtml(item.kind)}">${item.kind === 'income' ? '+' : '−'}${escapeHtml(item.amount)}</div></article>`;
}

function renderDashboard(data) {
  dashboardData = data;
  const primary = data.currency_totals?.find(item => item.currency === 'KZT') || data.currency_totals?.[0] || {balance:data.balance,income:data.income,expense:data.expense};
  $('balance').textContent = primary.balance;
  $('income').textContent = `+${primary.income}`;
  $('expense').textContent = `−${primary.expense}`;
  $('periodLabel').textContent = data.period_label;
  $('historyTitle').textContent = data.period_label;
  $('historySubtitle').textContent = `Операции: ${data.period_label.toLowerCase()}`;
  $('currencyTotals').innerHTML = (data.currency_totals || []).map(item => `<span class="currency-chip">${escapeHtml(item.currency)} · ${escapeHtml(item.balance)}</span>`).join('');

  const accounts = data.accounts || [];
  const accountMarkup = accounts.length ? accounts.map(accountCard).join('') : '<div class="empty-state">Счетов пока нет.<br>Создайте первый, чтобы начать учёт.</div>';
  $('accounts').innerHTML = accountMarkup;
  $('accountsFull').innerHTML = accountMarkup;
  document.querySelectorAll('[data-edit-account]').forEach(button => button.addEventListener('click', () => openEditAccount(Number(button.dataset.editAccount))));

  const categories = data.top_categories || [];
  const max = Math.max(...categories.map(item => Number(item.amount_minor) || 0), 1);
  $('topCategories').innerHTML = categories.length ? categories.map(item => `<div class="chart-row"><div class="chart-meta"><span>${escapeHtml(item.label)}</span><span>${escapeHtml(item.amount)}</span></div><div class="chart-track"><div class="chart-fill" style="width:${Math.max(8,(Number(item.amount_minor)||0)/max*100)}%"></div></div></div>`).join('') : '<div class="empty-state">Здесь появится структура расходов за выбранный период.</div>';

  const history = data.history || [];
  const empty = '<div class="empty-state">Операций за этот период пока нет.</div>';
  $('historyPreview').innerHTML = history.length ? history.slice(0,5).map(historyItem).join('') : empty;
  $('historyFull').innerHTML = history.length ? history.map(historyItem).join('') : empty;
}

async function loadDashboard() {
  const params = new URLSearchParams({period:activePeriod});
  if (activePeriod === 'custom') { params.set('start', customStart); params.set('end', customEnd); }
  $('syncState').innerHTML = '<i></i>Обновляем';
  const data = await api(`/api/dashboard?${params}`);
  renderDashboard(data);
  $('syncState').innerHTML = '<i></i>Синхронизировано';
  return data;
}

async function enterApplication() {
  await loadDashboard();
  const me = await api('/api/me').catch(() => ({can_sign_out:false}));
  $('signOutButton').classList.toggle('hidden', isMiniApp || isShortcut || !me.can_sign_out);
  $('profileButton').classList.toggle('hidden', !authSession);
  renderSettings();
  showOnly('appScreen');
  rememberActivity();
}

function navigate(section) {
  document.querySelectorAll('[data-view]').forEach(view => view.classList.toggle('hidden', view.dataset.view !== section));
  document.querySelectorAll('[data-section]').forEach(button => button.classList.toggle('active', button.dataset.section === section));
  scrollTo({top:0,behavior:'smooth'});
}

document.querySelectorAll('[data-section]').forEach(button => button.addEventListener('click', () => navigate(button.dataset.section)));

function setAuthMode(mode) {
  authMode = mode;
  $('authForm').classList.remove('hidden');
  $('recoveryForm').classList.add('hidden');
  $('authTabs').classList.remove('hidden');
  document.querySelectorAll('[data-auth-mode]').forEach(button => button.classList.toggle('active', button.dataset.authMode === mode));
  const signup = mode === 'signup';
  $('authNameLabel').classList.toggle('hidden', !signup);
  $('authPasswordConfirmLabel').classList.toggle('hidden', !signup);
  $('authPasswordConfirm').required = signup;
  $('authPassword').autocomplete = signup ? 'new-password' : 'current-password';
  $('authTitle').textContent = signup ? 'Создайте аккаунт' : 'С возвращением';
  $('authDescription').textContent = signup ? 'Почта нужна для входа и восстановления доступа.' : 'Войдите, чтобы продолжить вести финансы.';
  $('authSubmit').textContent = signup ? 'Создать аккаунт' : 'Войти';
  $('forgotPassword').classList.toggle('hidden', signup);
  setStatus('authStatus');
}

document.querySelectorAll('[data-auth-mode]').forEach(button => button.addEventListener('click', () => setAuthMode(button.dataset.authMode)));
document.querySelectorAll('[data-password-toggle]').forEach(button => button.addEventListener('click', () => {
  const input = $(button.dataset.passwordToggle);
  const visible = input.type === 'text';
  input.type = visible ? 'password' : 'text';
  button.textContent = visible ? 'Показать' : 'Скрыть';
  button.setAttribute('aria-label', visible ? 'Показать пароль' : 'Скрыть пароль');
}));

$('authForm').addEventListener('submit', async event => {
  event.preventDefault();
  if (!authClient) return setStatus('authStatus','Вход временно недоступен','error');
  const email = $('authEmail').value.trim().toLowerCase();
  const password = $('authPassword').value;
  if (!email || password.length < 8) return setStatus('authStatus','Проверьте email и пароль — минимум 8 символов','error');
  if (authMode === 'signup' && password !== $('authPasswordConfirm').value) return setStatus('authStatus','Пароли не совпадают','error');
  $('authSubmit').disabled = true;
  setStatus('authStatus', authMode === 'signup' ? 'Создаём аккаунт…' : 'Входим…');
  try {
    const credentials = {email,password};
    let result = authMode === 'signup'
      ? await authClient.signUp.email({...credentials,name:$('authName').value.trim() || email.split('@')[0]})
      : await authClient.signIn.email(credentials);
    if (result?.error) throw new Error(friendlyError(result.error));
    if (authMode === 'signup' && !result?.data?.session) {
      result = await authClient.signIn.email(credentials);
      if (result?.error) throw new Error(friendlyError(result.error));
    }
    await refreshAuthToken(3);
    if (!authToken) throw new Error('Сессию не удалось открыть. Попробуйте войти ещё раз.');
    await api('/api/auth/session');
    localStorage.setItem(ACTIVITY_KEY,String(Date.now()));
    setStatus('authStatus','Готово','success');
    await enterApplication();
  } catch (error) { setStatus('authStatus',friendlyError(error),'error'); }
  finally { $('authSubmit').disabled = false; }
});

$('forgotPassword').addEventListener('click', () => {
  $('recoveryEmail').value = $('authEmail').value.trim();
  $('authForm').classList.add('hidden');
  $('authTabs').classList.add('hidden');
  $('recoveryForm').classList.remove('hidden');
  setStatus('recoveryStatus');
});
$('backToSignIn').addEventListener('click', () => setAuthMode('signin'));
$('recoveryForm').addEventListener('submit', async event => {
  event.preventDefault();
  const email = $('recoveryEmail').value.trim().toLowerCase();
  $('recoverySubmit').disabled = true;
  setStatus('recoveryStatus','Отправляем письмо…');
  try {
    const result = await authClient.requestPasswordReset({email,redirectTo:`${location.origin}/?reset=1`});
    if (result?.error) throw new Error(result.error.message || 'Не удалось отправить письмо');
    setStatus('recoveryStatus','Если аккаунт существует, письмо уже отправлено. Проверьте также папку «Спам».','success');
  } catch (error) { setStatus('recoveryStatus',friendlyError(error),'error'); }
  finally { $('recoverySubmit').disabled = false; }
});

$('resetForm').addEventListener('submit', async event => {
  event.preventDefault();
  const password = $('resetPassword').value;
  if (password !== $('resetPasswordConfirm').value) return setStatus('resetStatus','Пароли не совпадают','error');
  const token = new URLSearchParams(location.search).get('token') || '';
  try {
    const result = await authClient.resetPassword({newPassword:password,token});
    if (result?.error) throw new Error(result.error.message || 'Ссылка недействительна или устарела');
    setStatus('resetStatus','Пароль изменён. Теперь войдите с новым паролем.','success');
    history.replaceState({},'',location.pathname);
    setTimeout(() => $('resetDialog').close(),1200);
  } catch (error) { setStatus('resetStatus',friendlyError(error),'error'); }
});

async function loadOptions() {
  const data = await api(`/api/options?kind=${activeKind}`);
  $('categorySelect').innerHTML = '<option value="">Выберите категорию</option>' + data.categories.map(item => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`).join('');
  $('accountSelect').innerHTML = '<option value="">Выберите счёт</option>' + data.accounts.map(item => `<option value="${item.id}" data-currency="${escapeHtml(item.currency)}">${escapeHtml(item.name)} · ${escapeHtml(item.balance)}</option>`).join('');
  $('transactionCurrencySymbol').textContent = '₸';
}

async function openTransaction(kind = 'expense') {
  activeKind = kind;
  $('transactionStatus').textContent = '';
  document.querySelectorAll('[data-kind]').forEach(button => button.classList.toggle('active', button.dataset.kind === kind));
  $('transactionTitle').textContent = kind === 'expense' ? 'Новый расход' : 'Новый доход';
  $('sheetBackdrop').classList.remove('hidden');
  $('transactionSheet').classList.add('open');
  $('transactionSheet').setAttribute('aria-hidden','false');
  await loadOptions().catch(error => setStatus('transactionStatus',friendlyError(error),'error'));
  setTimeout(() => $('amountInput').focus(),250);
}

function closeTransaction() {
  $('sheetBackdrop').classList.add('hidden');
  $('transactionSheet').classList.remove('open');
  $('transactionSheet').setAttribute('aria-hidden','true');
}

document.querySelectorAll('[data-new-kind]').forEach(button => button.addEventListener('click', () => openTransaction(button.dataset.newKind || 'expense')));
$('mobileFab').addEventListener('click', () => openTransaction('expense'));
$('closeTransaction').addEventListener('click', closeTransaction);
$('sheetBackdrop').addEventListener('click', closeTransaction);
document.querySelectorAll('[data-kind]').forEach(button => button.addEventListener('click', async () => {
  activeKind = button.dataset.kind;
  $('transactionTitle').textContent = activeKind === 'expense' ? 'Новый расход' : 'Новый доход';
  document.querySelectorAll('[data-kind]').forEach(item => item.classList.toggle('active',item === button));
  await loadOptions();
}));
$('accountSelect').addEventListener('change', () => {
  const currency = $('accountSelect').selectedOptions[0]?.dataset.currency || 'KZT';
  $('transactionCurrencySymbol').textContent = currencySymbols[currency] || currency;
});
$('transactionForm').addEventListener('submit', async event => {
  event.preventDefault();
  $('saveTransaction').disabled = true;
  setStatus('transactionStatus','Сохраняем…');
  try {
    await api('/api/transactions',{method:'POST',body:JSON.stringify({kind:activeKind,amount:$('amountInput').value,category:$('categorySelect').value,account_id:$('accountSelect').value,note:$('noteInput').value})});
    event.currentTarget.reset();
    tg?.HapticFeedback?.notificationOccurred('success');
    await loadDashboard();
    setStatus('transactionStatus','Операция сохранена','success');
    toast('Операция сохранена');
    setTimeout(closeTransaction,500);
  } catch (error) { setStatus('transactionStatus',friendlyError(error),'error'); tg?.HapticFeedback?.notificationOccurred('error'); }
  finally { $('saveTransaction').disabled = false; }
});

function openAccountDialog() {
  $('accountForm').reset();
  accountType = 'card';
  $('accountBalance').value = '0';
  $('accountCurrencySymbol').textContent = '₸';
  $('accountCurrency').disabled = false;
  $('accountCurrencyHint').classList.add('hidden');
  $('accountDialog').dataset.mode = 'create';
  $('accountDialog').dataset.accountId = '';
  $('accountDialogEyebrow').textContent = 'Добавление';
  $('accountDialogTitle').textContent = 'Новый счёт';
  $('accountSubmit').textContent = 'Создать счёт';
  $('depositFields').classList.add('hidden');
  document.querySelectorAll('[data-account-type]').forEach(button => button.classList.toggle('active',button.dataset.accountType === 'card'));
  setStatus('accountStatus');
  $('accountDialog').showModal();
}

function openEditAccount(accountId) {
  const item = dashboardData?.accounts?.find(account => Number(account.id) === Number(accountId));
  if (!item) return;
  $('accountForm').reset();
  accountType = item.account_type || 'card';
  $('accountDialog').dataset.mode = 'edit';
  $('accountDialog').dataset.accountId = String(item.id);
  $('accountDialogEyebrow').textContent = 'Редактирование';
  $('accountDialogTitle').textContent = item.name;
  $('accountSubmit').textContent = 'Сохранить изменения';
  $('accountName').value = item.name;
  $('accountBalance').value = String((Number(item.balance_minor) || 0) / 100).replace('.', ',');
  $('accountCurrency').value = item.currency;
  $('accountCurrency').disabled = true;
  $('accountCurrencyHint').classList.remove('hidden');
  $('accountCurrencySymbol').textContent = currencySymbols[item.currency] || item.currency;
  $('nominalRate').value = item.nominal_interest_rate_value ?? '';
  $('effectiveRate').value = item.effective_interest_rate_value ?? '';
  $('dailyInterest').checked = Boolean(item.daily_interest_enabled);
  $('depositFields').classList.toggle('hidden',accountType !== 'deposit');
  document.querySelectorAll('[data-account-type]').forEach(button => button.classList.toggle('active',button.dataset.accountType === accountType));
  setStatus('accountStatus');
  $('accountDialog').showModal();
}
$('newAccount').addEventListener('click',openAccountDialog);
$('quickNewAccount').addEventListener('click',openAccountDialog);
document.querySelectorAll('[data-create-account]').forEach(button => button.addEventListener('click',openAccountDialog));
document.querySelectorAll('[data-account-type]').forEach(button => button.addEventListener('click', () => {
  accountType = button.dataset.accountType;
  document.querySelectorAll('[data-account-type]').forEach(item => item.classList.toggle('active',item === button));
  $('depositFields').classList.toggle('hidden',accountType !== 'deposit');
}));
$('accountCurrency').addEventListener('change', () => $('accountCurrencySymbol').textContent = currencySymbols[$('accountCurrency').value]);
$('accountForm').addEventListener('submit', async event => {
  event.preventDefault();
  setStatus('accountStatus','Создаём счёт…');
  try {
    const editing = $('accountDialog').dataset.mode === 'edit';
    const accountId = $('accountDialog').dataset.accountId;
    await api(editing ? `/api/accounts/${accountId}` : '/api/accounts',{method:editing ? 'PATCH' : 'POST',body:JSON.stringify({name:$('accountName').value,balance:$('accountBalance').value,account_type:accountType,currency:$('accountCurrency').value,nominal_interest_rate:$('nominalRate').value,effective_interest_rate:$('effectiveRate').value,daily_interest_enabled:$('dailyInterest').checked})});
    $('accountDialog').close();
    await loadDashboard();
    toast(editing ? 'Счёт обновлён' : 'Счёт создан');
  } catch (error) { setStatus('accountStatus',friendlyError(error),'error'); }
});

$('newCategory').addEventListener('click', () => { setStatus('categoryStatus'); $('categoryForm').reset(); $('categoryDialog').showModal(); });
$('categoryForm').addEventListener('submit', async event => {
  event.preventDefault();
  setStatus('categoryStatus','Добавляем…');
  try {
    await api('/api/categories',{method:'POST',body:JSON.stringify({kind:activeKind,name:$('categoryName').value})});
    $('categoryDialog').close();
    await loadOptions();
    toast('Категория добавлена');
  } catch (error) { setStatus('categoryStatus',friendlyError(error),'error'); }
});

document.querySelectorAll('[data-close-dialog]').forEach(button => button.addEventListener('click', () => $(button.dataset.closeDialog).close()));

document.querySelectorAll('[data-period]').forEach(button => button.addEventListener('click', async () => {
  const period = button.dataset.period;
  if (period === 'custom') { $('customPeriod').classList.remove('hidden'); return; }
  activePeriod = period;
  $('customPeriod').classList.add('hidden');
  document.querySelectorAll('[data-period]').forEach(item => item.classList.toggle('active',item === button));
  await loadDashboard().catch(error => toast(friendlyError(error),'error'));
}));
$('applyPeriod').addEventListener('click', async () => {
  customStart = $('periodStart').value;
  customEnd = $('periodEnd').value;
  if (!customStart || !customEnd) return toast('Выберите обе даты','error');
  activePeriod = 'custom';
  document.querySelectorAll('[data-period]').forEach(button => button.classList.toggle('active',button.dataset.period === 'custom'));
  await loadDashboard().catch(error => toast(friendlyError(error),'error'));
});
$('toggleBalances').addEventListener('click', () => document.body.classList.toggle('privacy-mode'));

function renderSettings() {
  if (authSession) {
    $('settingsContent').innerHTML = `<article class="settings-card"><span class="eyebrow">Аккаунт</span><h3>${escapeHtml(authSession.user?.email || 'Email-аккаунт')}</h3><p>Пароль, безопасность и привязка Telegram находятся в личном кабинете.</p><button id="openProfileSettings" class="button button-primary" type="button">Открыть личный кабинет</button></article><article class="settings-card"><span class="eyebrow">Сессия</span><h3>Автовыход через 3 дня</h3><p>Если вы не пользуетесь сайтом три дня, вход будет завершён автоматически.</p><button id="settingsSignOut" class="button" type="button">Выйти сейчас</button></article><article class="settings-card full"><span class="eyebrow">Обновления</span><h3>Что нового в Purpose Finance</h3><p>История улучшений сайта и Telegram-бота.</p><button id="settingsUpdates" class="button" type="button">Посмотреть обновления</button></article>`;
    $('openProfileSettings').onclick = openProfile;
    $('settingsSignOut').onclick = () => signOut();
    $('settingsUpdates').onclick = openUpdates;
  } else {
    $('settingsContent').innerHTML = `<article class="settings-card"><span class="eyebrow">Telegram Mini App</span><h3>Вы вошли автоматически</h3><p>Аккаунт определён Telegram. Пароль и отдельная регистрация здесь не нужны.</p></article><article class="settings-card"><span class="eyebrow">Синхронизация</span><h3>Одна база с ботом</h3><p>Все изменения, сделанные здесь или в чате с ботом, отображаются одинаково.</p></article><article class="settings-card full"><span class="eyebrow">Обновления</span><h3>Что нового?</h3><p>Посмотрите последние изменения продукта.</p><button id="settingsUpdates" class="button" type="button">Открыть</button></article>`;
    $('settingsUpdates').onclick = openUpdates;
  }
}

function storeLink(token, expiresIn = 600) {
  telegramLinkToken = token;
  localStorage.setItem(LINK_KEY,JSON.stringify({token,expires:Date.now()+expiresIn*1000}));
}
function restoreLink() {
  try {
    const saved = JSON.parse(localStorage.getItem(LINK_KEY) || 'null');
    if (!saved?.token || saved.expires < Date.now()) return false;
    telegramLinkToken = saved.token;
    return true;
  } catch (_) { return false; }
}
function stopLinkPolling() { clearInterval(telegramLinkTimer); telegramLinkTimer = null; }
function clearLink() { telegramLinkToken = ''; localStorage.removeItem(LINK_KEY); stopLinkPolling(); }
function renderTelegramLink(profile) {
  const linked = profile?.telegram;
  $('telegramLinkTitle').textContent = linked ? 'Telegram привязан' : 'Telegram не привязан';
  $('telegramLinkDescription').textContent = linked ? (linked.display_name || (linked.username ? `@${linked.username}` : `Telegram ID ${linked.user_id}`)) : 'Привяжите бота, чтобы сайт и Telegram использовали одни данные.';
  $('linkTelegramButton').classList.toggle('hidden',Boolean(linked));
  $('checkTelegramLinkButton').classList.toggle('hidden',Boolean(linked));
  $('unlinkTelegramButton').classList.toggle('hidden',!linked);
}
async function checkLink() {
  if (!telegramLinkToken) return;
  const result = await api(`/api/profile/telegram/link/status?token=${encodeURIComponent(telegramLinkToken)}`);
  if (result.status === 'approved') { clearLink(); setStatus('profileStatus','Telegram успешно привязан','success'); renderTelegramLink(await api('/api/profile')); await loadDashboard(); }
  if (result.status === 'expired') { clearLink(); setStatus('profileStatus','Ссылка истекла. Создайте новую.','error'); }
}
function startLinkPolling() { stopLinkPolling(); telegramLinkTimer = setInterval(() => checkLink().catch(() => {}),2500); }
async function openProfile() {
  $('profileEmail').textContent = authSession?.user?.email || '—';
  $('profileDialog').showModal();
  setStatus('profileStatus');
  try { renderTelegramLink(await api('/api/profile')); if (restoreLink()) { startLinkPolling(); await checkLink(); } }
  catch (error) { setStatus('profileStatus',friendlyError(error),'error'); }
}
$('profileButton').addEventListener('click',openProfile);
$('linkTelegramButton').addEventListener('click',async () => {
  setStatus('profileStatus','Создаём запрос…');
  try { const data = await api('/api/profile/telegram/link/start',{method:'POST'}); storeLink(data.token,data.expires_in); startLinkPolling(); setStatus('profileStatus','Подтвердите привязку в чате с ботом и вернитесь сюда.'); location.href = data.telegram_url; }
  catch (error) { setStatus('profileStatus',friendlyError(error),'error'); }
});
$('checkTelegramLinkButton').addEventListener('click',async () => {
  if (!restoreLink()) return setStatus('profileStatus','Сначала нажмите «Привязать»','error');
  setStatus('profileStatus','Проверяем подтверждение…');
  await checkLink().catch(error => setStatus('profileStatus',friendlyError(error),'error'));
});
$('unlinkTelegramButton').addEventListener('click',async () => {
  if (!confirm('Отвязать Telegram? Финансовые данные останутся в аккаунте сайта.')) return;
  try { await api('/api/profile/telegram/unlink',{method:'POST'}); renderTelegramLink(await api('/api/profile')); setStatus('profileStatus','Telegram отвязан','success'); }
  catch (error) { setStatus('profileStatus',friendlyError(error),'error'); }
});
$('changePasswordForm').addEventListener('submit',async event => {
  event.preventDefault();
  const currentPassword = $('currentPassword').value;
  const newPassword = $('newPassword').value;
  if (newPassword !== $('newPasswordConfirm').value) return setStatus('changePasswordStatus','Пароли не совпадают','error');
  $('changePasswordSubmit').disabled = true;
  setStatus('changePasswordStatus','Меняем пароль…');
  try {
    const result = await authClient.changePassword({currentPassword,newPassword,revokeOtherSessions:$('revokeOtherSessions').checked});
    if (result?.error) throw new Error(result.error.message || 'Не удалось изменить пароль');
    await refreshAuthToken(3);
    event.currentTarget.reset();
    $('revokeOtherSessions').checked = true;
    setStatus('changePasswordStatus','Пароль изменён','success');
  } catch (error) { setStatus('changePasswordStatus',friendlyError(error),'error'); }
  finally { $('changePasswordSubmit').disabled = false; }
});

async function openUpdates() {
  $('updatesDialog').showModal();
  $('updatesList').innerHTML = '<div class="empty-state"><span class="spinner"></span></div>';
  try {
    const data = await api('/api/whats-new');
    $('updatesList').innerHTML = (data.releases || []).map(release => `<article class="release-card"><h3>Версия ${escapeHtml(release.version)}</h3><time>${escapeHtml(release.date)}</time><p>${escapeHtml(release.title)}</p><ul>${release.items.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></article>`).join('');
  } catch (error) { $('updatesList').innerHTML = `<div class="empty-state">${escapeHtml(friendlyError(error))}</div>`; }
}
$('whatsNewButton').addEventListener('click',openUpdates);
$('signOutButton').addEventListener('click',() => signOut());
$('retryMiniApp').addEventListener('click',() => location.reload());

async function boot() {
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/service-worker.js?v=8').catch(() => {});
  $('todayLabel').textContent = new Intl.DateTimeFormat('ru-RU',{weekday:'long',day:'numeric',month:'long'}).format(new Date());
  try {
    if (isMiniApp || isShortcut) {
      await enterApplication();
      return;
    }
    const configResponse = await fetch('/api/config');
    const config = await configResponse.json();
    if (!config.auth_enabled || !window.PurposeAuthBundle?.createClient) throw new Error('Сервис входа временно недоступен');
    authClient = window.PurposeAuthBundle.createClient(config.auth_url);
    await refreshAuthToken(2);
    if (authSession && sessionInactive()) { await signOut({inactive:true}); return; }
    const params = new URLSearchParams(location.search);
    if (params.get('reset') === '1' || params.get('token')) $('resetDialog').showModal();
    if (authToken) {
      try { await api('/api/auth/session'); await enterApplication(); return; }
      catch (_) { authSession = null; authToken = ''; }
    }
    showOnly('authScreen');
  } catch (error) {
    $('bootMessage').textContent = friendlyError(error);
    if (isMiniApp) showOnly('miniAppError');
    else { showOnly('authScreen'); setStatus('authStatus',friendlyError(error),'error'); }
  }
}

document.addEventListener('visibilitychange',() => {
  if (!document.hidden && telegramLinkToken) checkLink().catch(() => {});
});

boot();
