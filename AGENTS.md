# 项目开发规范（强制 · 全语言通用）

本项目的所有代码必须严格遵守以下规范。**先读本文件再写代码**，任何语言、任何文件不可例外。

## 0. 通用工程原则（所有语言）

- **单一职责**：一个函数/方法/命令只做一件事；超长函数必须拆分
- **防屎山铁律**：不生成"临时凑合"代码；不留死代码；重复逻辑必须抽取复用
- **可读性优先**：写给人看的代码，其次才是机器；别人（或未来的你）10 秒内能看懂
- **类型意识**：有类型系统的语言必须显式标注类型；动态语言参数也要标注（Python 用注解）
- **改动可验证**：每次改动后必须运行编译/语法检查 + 相关测试；每完成一个功能立即 git commit
- 提交身份：`kuoleroy <kuoleroy@outlook.com>`

## 1. 命名（所有语言）

- 完整语义英文命名；禁止 `tmp`、`res`、`data`、`arr`、`item`、`obj`、`fn`、`a`、`b`、`num`、`lst`、`dict1` 等模糊名称
- 布尔变量/属性以 `is_` / `has_` / `can_`（Python）或 `is` / `has` / `can`（驼峰语言）开头
- 集合变量用名词复数（`user_record_list` / `userRecords`）
- 常量全大写（`SAVE_OUTPUT_PATH` / `MAX_RETRY_COUNT`）
- 函数/方法动词开头（`parse_pdf_toc` / `createUser`），类用名词
- 禁止过度简写；行业通用缩写除外（`err`、`ctx`、`pdf`、`dpi`）

## 2. 代码结构

- 魔法数字全部抽为具名常量（`0.6`、`500`、`60000` 必须命名）
- 正则/复杂表达式抽为模块级常量
- 配置（路径、超时、阈值）集中在常量区或配置文件，不散落代码中

## 3. 注释

- 关键逻辑写注释说明「设计思路 / 边界情况 / 为什么这样做」，不复述代码行为
- 复杂函数/方法必须写 docstring / JSDoc / 注释头（中文）

## 4. 异常与错误处理

- IO、网络、外部调用必须 try-catch / 检查错误返回值，不裸调用
- 区分异常类型，禁止万能 `catch (Exception) {}` / `except: pass`；确需吞错的场景须注明原因
- 资源（文件、连接、句柄）必须用语言惯用的自动释放机制（with/try-with/RAII/defer）

---

## 5. 各语言细则

### 5.1 Python
- 风格：PEP8，4 空格缩进，snake_case；类型注解必写（函数参数与返回值）
- 文件顶部有模块 docstring；常量大写；`typing` 注解用 `List`/`Optional`/`Callable` 等
- 禁止 `except: pass`；文件操作必须 try-finally / with 保证关闭
- GUI 代码禁止阻塞主线程（多进程/线程池管理后台任务）

### 5.2 JavaScript / TypeScript
- 变量/函数 camelCase，类/组件/类型 PascalCase，常量 UPPER_SNAKE_CASE（`const` 优先，禁 `var`）
- TS：严格模式开启，禁止 `any`；接口 `I` 前缀不加，类型名用语义名词；函数参数和返回值必须标注类型
- Promise/async 必须处理异常（`.catch` / try-catch），禁止未处理的 promise 拒绝

### 5.3 Go
- 官方风格（gofmt）；导出函数/变量首字母大写，内部小写
- 错误处理：检查每个返回 error，禁止 `_ =` 丢弃；defer 释放资源
- 禁止 panic 作为常规错误流；结构体优先于裸 map 传递数据

### 5.4 Shell / PowerShell
- 禁止无意义变量名；命令路径与用户输入必须加引号防注入
- Shell：脚本头 `set -euo pipefail`；PowerShell：脚本头 `$ErrorActionPreference = 'Stop'`
- 每条命令判断失败（`$?` / `$LASTEXITCODE`），失败即中止并输出明确错误信息

### 5.5 C / C++
- snake_case 函数与变量，宏/常量全大写，类 PascalCase
- 资源管理用 RAII / 智能指针，禁止裸 new/delete 泄露路径
- 错误用返回码 / 异常（C++）区分类型，禁止吞错

### 5.6 Java / Kotlin
- camelCase 变量/方法，PascalCase 类，常量全大写；字段尽量 `private final` / `val`
- 异常区分类型（IOException / IllegalArgumentException 等），禁止 `catch (Exception e) {}`
- 流式资源用 try-with-resources

### 5.7 SQL
- 关键字大写，表/列名 snake_case 语义命名；禁止 `SELECT *`
- 索引、事务、参数化查询（防注入）是必备项

---

## 6. 禁止行为（重申）

- 不写临时一次性代码混入正式代码
- 不省略类型标注
- 不出现重复逻辑
- 输出代码前自行检索模糊命名（`tmp`/`item`/`data` 等），发现即重构
