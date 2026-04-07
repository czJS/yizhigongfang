# 云侧 HTTPS 正式迁移清单

> 适用目标：将 `auth.miaoyichuhai.com` 与 `admin.miaoyichuhai.com` 的公网 HTTPS 终止，从当前 ECS 自管入口迁移到更标准的阿里云托管入口，解决 Node / Electron 对现网 TLS 入口握手失败的问题。

---

## 1. 当前结论

- 当前 ECS 上的 Nginx 证书已切换验证过标准公开 CA 证书链。
- 即便换成 Let’s Encrypt，`Node.js` / `Electron` 直连 `auth.miaoyichuhai.com` 和 `admin.miaoyichuhai.com` 仍会出现 `ECONNRESET` / `ERR_CONNECTION_CLOSED`。
- 因此问题不在应用代码，也不在当前服务器证书文件本身，而在更外层的公网 HTTPS 入口兼容性。

结论：

- 正式修复方案应迁移到阿里云 `ALB`。
- 在 `ALB` 前完成切流前，桌面端保留当前兼容回退方案。

---

## 2. 目标架构

- 用户访问 `auth.miaoyichuhai.com` / `admin.miaoyichuhai.com`
- DNS 解析到阿里云 `ALB`
- `ALB` 负责标准 TLS 握手与证书托管
- `ALB` 回源到 ECS `Nginx`
- ECS `Nginx` 再反代到 `auth_service`

建议：

- `ALB` 监听器：
  - `80`：统一跳转到 `443`
  - `443`：绑定标准证书
- 转发规则：
  - `Host = auth.miaoyichuhai.com` -> ECS 对应后端服务
  - `Host = admin.miaoyichuhai.com` -> ECS 对应后端服务

---

## 3. 迁移前准备

1. 在阿里云控制台创建 `ALB` 实例，地域与 ECS 保持一致。
2. 创建服务器组，将当前 ECS 加入后端服务器组。
3. 规划健康检查：
   - `auth.miaoyichuhai.com` 使用 `/api/health`
   - `admin.miaoyichuhai.com` 可复用 `/api/health`
4. 准备证书：
   - 优先使用阿里云证书服务签发或托管证书
   - 证书需覆盖：
     - `auth.miaoyichuhai.com`
     - `admin.miaoyichuhai.com`
5. 降低 DNS TTL 到 `300s`，便于切换和回滚。

---

## 4. 阿里云控制台执行步骤

### 4.0 进入位置

1. 登录阿里云控制台。
2. 打开 `应用型负载均衡 ALB`。
3. 进入与你当前 ECS 相同地域。
4. 在左侧依次查看：
   - `实例`
   - `服务器组`
   - `监听`
   - `转发规则`

### 4.1 创建监听

1. 在 `ALB 实例详情` 中点击 `监听`。
2. 点击 `创建监听`。
3. 创建第一个监听：
   - 监听协议：`HTTP`
   - 监听端口：`80`
4. 动作选择：
   - 若控制台支持重定向动作，直接选择 `重定向到 HTTPS 443`
   - 若当前版本控制台不支持直接重定向，可先创建一个默认转发，后续再补跳转策略
5. 再次点击 `创建监听`。
6. 创建第二个监听：
   - 监听协议：`HTTPS`
   - 监听端口：`443`
7. 在证书位置选择：
   - 已有阿里云证书：直接选择
   - 非阿里云证书：先到证书服务上传后再回来选择
8. 默认服务器组先指向当前 ECS 对应服务器组。

### 4.2 配置转发规则

1. 打开 `443` 监听详情。
2. 点击 `转发规则`。
3. 点击 `添加转发规则`。
4. 添加第一条：
   - 匹配条件：`Host`
   - Host 值：`auth.miaoyichuhai.com`
   - 转发动作：转发到当前 ECS 服务器组
5. 再添加第二条：
   - 匹配条件：`Host`
   - Host 值：`admin.miaoyichuhai.com`
   - 转发动作：转发到当前 ECS 服务器组
6. 若控制台要求默认规则，默认规则也先指向同一服务器组，避免没有命中时直接 5xx。

### 4.2.1 服务器组建议

服务器组建议这样配：

- 类型：`ECS 服务器组`
- 后端协议：第一阶段优先 `HTTP`
- 后端端口：
  - 若 ECS 上 `Nginx` 对外监听 `80`，这里填 `80`
  - 若你希望 `ALB` 直接回到 ECS `443`，可第二阶段再调整
- 负载均衡算法：默认即可
- 会话保持：先关闭，避免排查期间引入额外变量

### 4.3 回源协议建议

- 第一阶段建议 `ALB -> ECS` 先走 `HTTP:80`
- 等域名握手问题彻底验证通过后，再评估是否收敛为 `ALB -> ECS HTTPS`

这样做的原因：

- 先把“公网 TLS 兼容问题”与“内网回源 TLS”拆开
- 更容易定位问题，回滚也更简单

### 4.4 安全组与放通

- 放通 `ALB -> ECS` 的回源端口
- 若 `Nginx` 仅允许本机或旧来源，需要同步补充 `ALB` 网段

更细一点检查：

1. 打开 ECS 的安全组。
2. 检查入方向规则：
   - 若 `ALB -> ECS` 走 `80`，确认 `80` 已允许来自 `ALB`
   - 若 `ALB -> ECS` 走 `443`，确认 `443` 已允许来自 `ALB`
3. 若当前安全组只允许 `0.0.0.0/0`，虽然能通，但上线后建议再收敛到 `ALB` 来源。
4. 若系统里还有 `ufw`、`iptables`、`firewalld`，也要一并核对，不要只看阿里云安全组。

---

## 5. Nginx 侧配合项

切流前建议确认：

- `auth.miaoyichuhai.com` 的 `/api/health` 返回 `200`
- `admin.miaoyichuhai.com` 的首页和 `/api/admin/me` 路由正常
- `Host` 头转发逻辑仍按域名区分
- 不再依赖公网 443 上的旧证书兼容性

建议保留当前 ECS 上的 Nginx 配置和证书文件，作为回滚兜底，不要先删除。

建议额外确认：

1. `server_name` 中仍包含：
   - `auth.miaoyichuhai.com`
   - `admin.miaoyichuhai.com`
2. 反代时保留：
   - `Host`
   - `X-Forwarded-For`
   - `X-Forwarded-Proto`
3. 应用内部不要把来源域名写死成旧公网入口 IP。

---

## 6. 切换步骤

1. 在 `ALB` 上完成监听、规则、证书、健康检查。
2. 在阿里云 DNS 中，把：
   - `auth.miaoyichuhai.com`
   - `admin.miaoyichuhai.com`
   改为指向 `ALB` 公网地址。
3. 等待 TTL 生效。
4. 立即执行以下验收：
   - 浏览器打开 `https://admin.miaoyichuhai.com`
   - 浏览器走完整管理员登录
   - 桌面端发送邮箱验证码
   - 桌面端邮箱验证码登录
   - 桌面端已授权账号直接进入
   - 桌面端未授权账号登录后被任务门禁阻断
   - Electron 探针访问 `https://auth.miaoyichuhai.com/api/health`

### 6.1 DNS 操作细化

1. 打开阿里云 `云解析 DNS`。
2. 找到主域名 `miaoyichuhai.com`。
3. 找到两条记录：
   - `auth`
   - `admin`
4. 记录类型保持与当前一致，通常是：
   - `A` 记录直接指向 ALB 公网 IP，或
   - `CNAME` 指向 ALB 分配域名
5. TTL 改为 `300`。
6. 先确认 `ALB` 健康检查正常，再保存解析。

### 6.2 切换后第一时间检查

建议按这个顺序检查：

1. 浏览器打开 `https://admin.miaoyichuhai.com`
2. 浏览器开发者工具看是否还能正常返回 HTML 和 API
3. 本机执行 `curl -Iv https://auth.miaoyichuhai.com/api/health`
4. 本机执行 Node 探针
5. 本机执行 Electron 探针
6. 最后再验证桌面端完整登录流

---

## 7. 验收标准

- `Node.js https.get()` 可稳定访问 `auth` / `admin`
- Electron `net.request` 不再出现 `ERR_CONNECTION_CLOSED`
- 浏览器、桌面端、自动化探针三者结果一致
- 不再依赖：
  - 直连 ECS IP
  - 特殊证书放行
  - 桌面端兼容域名回退

建议把下面这几个结果截图或留日志，便于后续复盘：

- `ALB` 健康检查绿灯
- 浏览器访问成功
- Node 探针成功
- Electron 探针成功
- 桌面端验证码登录成功

---

## 8. 回滚方案

若切换后异常：

1. 将 DNS 记录改回原公网入口
2. 保留桌面端现有兼容回退逻辑
3. 检查 `ALB`：
   - 监听
   - 证书
   - 健康检查
   - 回源端口
   - 安全组
4. 修复后重新小流量验证

### 8.1 回滚触发条件建议

出现以下任一情况就建议立即回滚：

- 管理后台首页打不开
- Electron 探针仍然握手失败
- 桌面端发送验证码失败率明显升高
- 管理员登录或用户登录出现持续 5xx

---

## 9. 切换后代码收敛建议

当 `ALB` 已稳定通过 Electron 探针后，再做以下收敛：

1. 移除桌面端 `IP` 兼容回退地址
2. 移除 Electron 主进程中对兼容 IP 的特殊证书放行
3. 保留 `electron_tls_probe` 作为发布门禁
4. 发布前用真实 Electron 网络栈再跑一轮回归

---

## 10. 当前推荐执行顺序

1. 先在阿里云创建 `ALB`
2. 完成证书绑定和健康检查
3. 小流量或低峰期切换 DNS
4. 通过 Electron 探针验收
5. 验收稳定后再移除桌面端兼容绕过
