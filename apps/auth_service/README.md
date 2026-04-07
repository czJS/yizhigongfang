# Auth Service

一期云端登录与授权服务。

## 目标

提供最小可用的：

- 邮箱验证码登录
- 登录会话
- 激活码兑换
- 设备绑定
- 简单后台接口

## 运行前准备

1. MySQL（RDS）已创建，白名单已放通 ECS 私网 IP。
2. Redis（Tair）已创建，白名单已放通 ECS 私网 IP。
3. SMTP / DirectMail 已准备好；如果暂时未完成，可先将 `AUTH_DEV_ECHO_CODES=1` 用于开发联调。

## 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 环境变量

复制 `.env.example` 到本地未入库文件，例如：

```bash
cp .env.example .env.local
```

然后把其中的密码、密钥、SMTP 用户名等替换成真实值。

## 启动

开发启动：

```bash
export $(grep -v '^#' .env.local | xargs)
python app.py
```

生产启动（推荐配合 `systemd + gunicorn`）：

```bash
export $(grep -v '^#' .env.local | xargs)
gunicorn -c gunicorn.conf.py app:app
```

默认监听：

- `0.0.0.0:8001`

## 主要接口

### 健康检查

- `GET /api/health`

### 用户登录

- `POST /api/auth/email/send-code`
- `POST /api/auth/email/login`
- `GET /api/auth/me`
- `POST /api/auth/logout`

### 授权

- `GET /api/license/current`
- `POST /api/license/redeem`
- `GET /api/license/devices`
- `POST /api/license/devices/unbind`

### 管理接口

管理接口使用请求头：

```text
X-Admin-Secret: <AUTH_ADMIN_SECRET>
```

接口包括：

- `GET /api/admin/users`
- `GET /api/admin/activation-codes`
- `POST /api/admin/activation-codes`
- `POST /api/admin/licenses/freeze`
- `POST /api/admin/licenses/extend`
- `POST /api/admin/devices/unbind`

## 最小联调示例

### 1. 发送验证码

```bash
curl -X POST http://127.0.0.1:8001/api/auth/email/send-code \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com"}'
```

### 2. 登录

```bash
curl -X POST http://127.0.0.1:8001/api/auth/email/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","code":"123456","device_id":"dev-001","device_name":"Mac Mini","platform":"macOS"}'
```

### 3. 创建激活码

```bash
curl -X POST http://127.0.0.1:8001/api/admin/activation-codes \
  -H 'Content-Type: application/json' \
  -H 'X-Admin-Secret: replace-with-admin-secret' \
  -d '{"count":5,"duration_days":30,"type":"monthly"}'
```
