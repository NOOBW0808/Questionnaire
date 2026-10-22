# 配置说明

`app.py`启动时读取本目录`.env`；`.env.example`为模板。若终端已有同名环境变量，它优先于`.env`。配置变更后需要重新启动应用。

| 配置项 | 默认值 | 使用说明 |
|---|---|---|
| `MONGODB_URI` | `mongodb://127.0.0.1:27018/?replicaSet=survey-rs` | 必须连接MongoDB 7+副本集；改端口或副本集名时与实际启动参数一致。 |
| `MONGODB_DATABASE` | `questionnaire_app` | 应用实际访问的库名。建议使用独立新库，例如`questionnaire_submission`；不自动覆盖不兼容的旧库。 |
| `APP_HOST` | `127.0.0.1` | HTTP监听地址，默认仅本机访问。 |
| `APP_PORT` | `5000` | 应用端口；占用时可改5001，并同步修改来源。 |
| `APP_ORIGIN` | `http://127.0.0.1:5000` | 浏览器请求来源；协议、主机、端口须与实际访问完全一致。使用localhost和127.0.0.1访问属于不同来源。 |
| `COOKIE_SECURE` | `false` | 本地HTTP使用false；HTTPS部署设true，否则安全Cookie不会在普通HTTP连接中携带。 |
| `SESSION_HOURS` | `24` | 登录有效小时数，实际限制为1～720。过期会话不依赖TTL删除即可失效。 |
| `CURSOR_KEY` | 未设置 | 可选Fernet密钥；省略时生成并持久化至`.local/cursor.key`。保留该文件可继续使用未过期分页游标；多实例需要一致的密钥。 |

如需显式生成Fernet密钥，可在已安装依赖的环境中执行，然后将输出填入`.env`的`CURSOR_KEY`：

```powershell
& '.\.venv\Scripts\python.exe' -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

副本集辅助脚本不读取`.env`中的`MONGODB_URI`；使用独立命令行参数`--port`、`--replica-set`和`--mongod`。`--mongod`省略时读取`MONGOD_EXE`环境变量，否则尝试`D:\MongoDB\bin\mongod.exe`。其数据目录始终位于本解压目录的`.local/mongodb-<port>/data`。应用连接参数必须与之对应。

示例：端口27019数据库、5001应用，使用独立库名。在`.env`中设置：

```dotenv
MONGODB_URI=mongodb://127.0.0.1:27019/?replicaSet=survey-submit-rs
MONGODB_DATABASE=questionnaire_submission
APP_HOST=127.0.0.1
APP_PORT=5001
APP_ORIGIN=http://127.0.0.1:5001
COOKIE_SECURE=false
SESSION_HOURS=24
```

本包不包含真实`.env`、虚拟环境、数据库文件、密码/会话、游标密钥或用户数据；这些内容在使用者机器上生成。`requirements-lock.txt`用于复现已有Python依赖版本，若其他解释器或平台无法安装其中的原生扩展，应先确认兼容的Python版本和wheel，不能将单纯“已安装”视为原生模块可导入。
