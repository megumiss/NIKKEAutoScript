document.addEventListener('DOMContentLoaded', () => {
    const apiUrlInput = document.getElementById('apiUrl');
    const autoSyncCb = document.getElementById('autoSync');
    const checkBtn = document.getElementById('checkBtn');
    const syncBtn = document.getElementById('syncBtn');
    const statusDot = document.getElementById('statusDot');
    const statusText = document.getElementById('statusText');
    const logMsg = document.getElementById('logMsg');
    const userInfoDiv = document.getElementById('userInfo'); // 新增：获取用户信息 DOM

    // --- 常量配置 ---
    const TARGET_DOMAIN = "https://www.blablalink.com";
    const DEFAULT_API_URL = "http://127.0.0.1:12271/api/nkas/config";

    const CONFIG_KEY = "BlaAuth.BlaAuth.Cookie";

    const COOKIES_TO_FETCH = [
        "game_openid",
        "game_channelid",
        "game_token",
        "game_gameid",
        "game_login_game",
        "game_adult_status",
        "game_user_name",
        "game_uid",
        "OptanonConsent",
    ];

    let isLoggedIn = false;

    // 1. 初始化：读取保存的 API 地址和自动同步状态
    chrome.storage.local.get(['savedApiUrl', 'autoSyncEnabled'], (result) => {
        apiUrlInput.value = result.savedApiUrl || DEFAULT_API_URL;
        autoSyncCb.checked = result.autoSyncEnabled || false; // 默认关闭

        // 读取配置完毕后，执行首次启动检查
        checkLoginStatus(true);
    });

    // 2. 监听输入框变化，自动保存
    apiUrlInput.addEventListener('blur', () => {
        const val = apiUrlInput.value.trim();
        if (val) {
            chrome.storage.local.set({ savedApiUrl: val });
        }
    });

    // 监听复选框变化，自动保存状态
    autoSyncCb.addEventListener('change', () => {
        chrome.storage.local.set({ autoSyncEnabled: autoSyncCb.checked });
    });

    // UI 更新辅助函数
    function updateStatusUI(state, text) {
        statusDot.className = `status-dot ${state}`;
        statusText.textContent = text;
        statusText.style.color = state === 'success' ? '#28a745' : (state === 'error' ? '#dc3545' : '#333');
    }

    function showLog(msg, isError = false) {
        logMsg.textContent = msg;
        logMsg.style.color = isError ? '#dc3545' : '#666';
    }

    // --- 检查登录状态 ---
    // 用来判断是否是插件刚打开时的检查
    async function checkLoginStatus(isStartup = false) {
        isLoggedIn = false;
        syncBtn.disabled = true;
        userInfoDiv.style.display = 'none'; // 检查前隐藏用户名
        updateStatusUI('checking', '正在检查登录状态...');
        showLog('');

        try {
            let missingCookies = [];
            let userName = ''; // 新增：用于暂存用户名

            for (const name of COOKIES_TO_FETCH) {
                const cookie = await chrome.cookies.get({
                    url: TARGET_DOMAIN,
                    name: name
                });
                if (!cookie || !cookie.value) {
                    missingCookies.push(name);
                } else if (name === 'game_user_name') {
                    // 新增：提取用户名。由于中文可能被 URL 编码，这里使用 decodeURIComponent 解码
                    userName = decodeURIComponent(cookie.value);
                }
            }

            if (missingCookies.length === 0) {
                isLoggedIn = true;
                updateStatusUI('success', '已登录');
                syncBtn.disabled = false;

                // 新增：显示用户名
                if (userName) {
                    userInfoDiv.textContent = `当前登录账户：${userName}`;
                    userInfoDiv.style.display = 'block';
                }

                // 如果是启动时，且勾选了自动同步，则直接发起同步
                if (isStartup && autoSyncCb.checked) {
                    syncCookies();
                }

            } else {
                updateStatusUI('error', '未登录或已失效');
                showLog(`缺失 Cookie: ${missingCookies.join(', ')}`, true);
            }
        } catch (error) {
            updateStatusUI('error', '检查异常');
            showLog(`读取 Cookie 失败: ${error.message}`, true);
        }
    }

    // --- 执行同步 ---
    async function syncCookies() {
        if (!isLoggedIn) {
            showLog("❌ 请先确保登录状态为绿色（已登录）", true);
            return;
        }

        const apiUrl = apiUrlInput.value.trim();

        if (!apiUrl.startsWith('http')) {
            showLog("❌ 错误：请输入有效的 http 或 https 地址", true);
            return;
        }

        syncBtn.disabled = true;
        showLog("⏳ 正在提取 Cookie 并发送...");

        try {
            let cookiePairs = [];
            for (const name of COOKIES_TO_FETCH) {
                const c = await chrome.cookies.get({ url: TARGET_DOMAIN, name: name });
                if (c) cookiePairs.push(`${c.name}=${c.value}`);
            }

            const cookieString = cookiePairs.join('; ');

            if (!cookieString) {
                showLog("❌ 提取失败：未找到指定的 Cookie 字段。", true);
                syncBtn.disabled = false;
                return;
            }

            // 构造 JSON Body，直接使用常量的 CONFIG_KEY
            const payload = {
                key: CONFIG_KEY,
                value: cookieString
            };

            const response = await fetch(apiUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            // 解析后端的 JSON 响应
            let resData;
            try {
                resData = await response.json();
            } catch (e) {
                throw new Error(`服务器返回了非 JSON 数据，状态码: ${response.status}`);
            }

            if (response.ok && resData.status === 'success') {
                showLog(`🎉 成功！${resData.message}`);
            } else {
                // 输出后端具体的报错信息
                showLog(`❌ 发送失败：${resData.message || '未知错误'}`, true);
            }

        } catch (error) {
            showLog(`❌ 网络或请求异常：${error.message}`, true);
        } finally {
            syncBtn.disabled = false;
        }
    }

    // 绑定事件
    checkBtn.addEventListener('click', () => checkLoginStatus(false));
    syncBtn.addEventListener('click', syncCookies);
});