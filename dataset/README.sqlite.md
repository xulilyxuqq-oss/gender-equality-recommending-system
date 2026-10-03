# SQLite 数据库

`recommender.sqlite3` 由当前目录中的三份 JSONL 数据构建：

- `user_info.jsonl`
- `course_info.jsonl`
- `user_course_interactions.jsonl`

课程相关数据分为两层：

- `courses`、`course_fields`、`course_prerequisites` 保留规范化原始数据；
- `course_information` 每门课程一行，合并课程类别和先修课程，并包含脚本生成的详细介绍与课程资料。

`course_information` 的字段如下：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `course_id` | TEXT | 课程 ID，同时关联 `courses.course_id` |
| `course_category` | JSON 数组 | 按原顺序合并的课程类别 |
| `detailed_description` | TEXT | 基于课程元数据生成的详细介绍 |
| `is_advanced` | INTEGER | 是否为高阶课程，取值为 0 或 1 |
| `prerequisites` | JSON 数组 | 先修课程 ID 与名称 |
| `course_materials` | JSON 数组 | 生成的结构化学习资料清单 |

重新构建前先备份或移走已有数据库，然后运行：

```bash
python3 dataset/build_sqlite.py
```

若数据库已经存在，只需新增或刷新 `course_information`，运行：

```bash
python3 dataset/populate_course_information.py
```

该脚本可重复运行；它会在一个事务中根据现有三张课程来源表重新生成全部课程信息。课程资料仅描述内部学习指南、知识清单、先修复习和实践练习，不会虚构教材名称或外部链接。

打开数据库：

```bash
sqlite3 dataset/recommender.sqlite3
```

也可以使用仓库中的压缩 SQL 转储在另一台电脑上重建：

```bash
gzip -dc dataset/recommender_dump.sql.gz | sqlite3 dataset/recommender.sqlite3
```

目标数据库文件应当不存在，以免与旧结构或旧数据冲突。

常用查询：

```sql
-- 查看各表数据量
SELECT * FROM metadata WHERE key LIKE '%_count';

-- 查看合并后的课程信息（JSON 字段保持数组结构）
SELECT
    course_id,
    json_extract(course_category, '$[0]') AS primary_category,
    detailed_description,
    is_advanced,
    prerequisites,
    course_materials
FROM course_information
LIMIT 10;

-- 查看某个用户的交互记录
SELECT *
FROM interaction_details
WHERE user_id = 'U_1000752'
ORDER BY comment DESC, course_id;

-- 比较男女用户的高阶课程交互比例
SELECT
    gender_code,
    COUNT(*) AS interaction_count,
    AVG(is_advanced) AS advanced_exposure_rate
FROM interaction_details
GROUP BY gender_code;
```
