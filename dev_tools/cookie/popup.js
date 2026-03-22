document.addEventListener('DOMContentLoaded', () => {
    const apiUrlInput = document.getElementById('apiUrl');
    const autoSyncCb = document.getElementById('autoSync');
    const checkBtn = document.getElementById('checkBtn');
    const syncBtn = document.getElementById('syncBtn');
    const statusDot = document.getElementById('statusDot');
    const statusText = document.getElementById('statusText');
    const logMsg = document.getElementById('logMsg');
    const userInfoDiv = document.getElementById('userInfo');

    // --- 常量配置 ---
    const TARGET_DOMAIN = "https://www.blablalink.com";
    const DEFAULT_API_URL = "http://127.0.0.1:12271/api/nkas/config";
    const CONFIG_KEY = "BlaAuth.BlaAuth.Cookie";

    // 必填 Cookie：缺失会导致判定为未登录
    const REQUIRED_COOKIES = [
        "game_openid",
        "game_channelid",
        "game_token",
        "game_gameid",
        "game_login_game",
        "game_adult_status",
        "game_uid",
        "OptanonConsent",
    ];

    // 选填 Cookie：缺失不影响判定，有的话一并收集
    const OPTIONAL_COOKIES = [
        "game_user_name"
    ];

    let isLoggedIn = false;

    // 1. 初始化：读取保存的 API 地址和自动同步状态
    chrome.storage.local.get(['savedApiUrl', 'autoSyncEnabled'], (result) => {
        apiUrlInput.value = result.savedApiUrl || DEFAULT_API_URL;
        autoSyncCb.checked = result.autoSyncEnabled || false;

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

    // 掩码脱敏辅助函数
    function maskString(str, type) {
        if (!str) return '';
        const len = str.length;

        if (type === 'uid') {
            // UID脱敏：保留前2位和后2位
            if (len <= 4) return str.charAt(0) + '***' + str.charAt(len - 1);
            return str.substring(0, 2) + '****' + str.substring(len - 2);
        } else {
            // 任意字符形式的用户名脱敏
            if (len <= 2) {
                return str.charAt(0) + '*';
            } else if (len <= 4) {
                return str.charAt(0) + '***' + str.charAt(len - 1);
            } else {
                // 大于4个字符，保留前2后2
                return str.substring(0, 2) + '***' + str.substring(len - 2);
            }
        }
    }

    // --- 检查登录状态 ---
    async function checkLoginStatus(isStartup = false) {
        isLoggedIn = false;
        syncBtn.disabled = true;
        userInfoDiv.style.display = 'none';
        updateStatusUI('checking', '正在检查登录状态...');
        showLog('');

        try {
            let missingCookies = [];
            let userName = '';
            let userId = '';

            // 1. 检查必填项并提取 UID
            for (const name of REQUIRED_COOKIES) {
                const cookie = await chrome.cookies.get({ url: TARGET_DOMAIN, name: name });
                if (!cookie || !cookie.value) {
                    missingCookies.push(name);
                } else if (name === 'game_uid') {
                    userId = cookie.value;
                }
            }

            // 2. 尝试获取选填项 (用户名)
            for (const name of OPTIONAL_COOKIES) {
                const cookie = await chrome.cookies.get({ url: TARGET_DOMAIN, name: name });
                if (cookie && cookie.value && name === 'game_user_name') {
                    userName = decodeURIComponent(cookie.value);
                }
            }

            if (missingCookies.length === 0) {
                isLoggedIn = true;
                updateStatusUI('success', '已登录');
                syncBtn.disabled = false;

                // 判断展示逻辑并脱敏
                let displayAccount = '未知用户';
                if (userName) {
                    displayAccount = maskString(userName, 'name');
                } else if (userId) {
                    displayAccount = `UID:${maskString(userId, 'uid')}`;
                }

                userInfoDiv.textContent = `当前登录账户：${displayAccount}`;
                userInfoDiv.style.display = 'block';

                if (isStartup && autoSyncCb.checked) {
                    syncCookies();
                }

            } else {
                updateStatusUI('error', '未登录或已失效');
                showLog(`缺失核心 Cookie: ${missingCookies.join(', ')}`, true);
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
            const ALL_COOKIES = [...REQUIRED_COOKIES, ...OPTIONAL_COOKIES];

            for (const name of ALL_COOKIES) {
                const c = await chrome.cookies.get({ url: TARGET_DOMAIN, name: name });
                if (c && c.value) {
                    cookiePairs.push(`${c.name}=${c.value}`);
                }
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