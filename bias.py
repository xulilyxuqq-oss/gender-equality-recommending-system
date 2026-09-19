from pathlib import Path
import pandas as pd

# 首先，获取当前脚本所在的目录路径
BASE_DIR = Path(__file__).resolve().parent

# JSONL 文件的每一行都是一个独立的 JSON 对象，因此需要使用 lines=True
df_user = pd.read_json(
    BASE_DIR / "dataset" / "user_info.jsonl", lines=True
)[["user_id", "gender_code"]]
df_course = pd.read_json(
    BASE_DIR / "dataset" / "course_info.jsonl", lines=True
)[["course_id"]]
df_interaction = pd.read_json(
    BASE_DIR / "dataset" / "user_course_interactions.jsonl", lines=True
)[["user_id", "course_id", "comment"]]


### 合并三个表，以及处理男女用户信息

# 把用户表和交互表按照user_id进行合并
df_user_interaction = pd.merge(df_user, df_interaction, on = "user_id", how = "inner")

# 把df_user_interaction和课程表按照course_id进行合并
df_user_course_interaction = pd.merge(df_user_interaction, df_course, on = "course_id", how = "inner")

# 统计男女用户数量
number_male = df_user[df_user["gender_code"] == 1]["user_id"].nunique()
number_female = df_user[df_user["gender_code"] == 2]["user_id"].nunique()

if number_male == 0 or number_female == 0:
    raise ValueError("One of the gender groups has zero users. Cannot compute bias.")

print(number_male, number_female)

# 计算UVR和IVB两个指标

# UVR指标计算 
commented = df_user_course_interaction[df_user_course_interaction["comment"] != 0]
number_male_comment = commented[commented["gender_code"] == 1]["user_id"].nunique()
number_female_comment = commented[commented["gender_code"] == 2]["user_id"].nunique()

print("男性的评论用户数：", number_male_comment,"女性的评论用户数：", number_female_comment)

if number_male_comment == 0 or number_female_comment == 0:
    raise ValueError("One of the gender groups has zero commenting users. Cannot compute bias.")

P_female = number_female_comment / number_female
P_male = number_male_comment / number_male

UVR = P_female / P_male
print("UVR指标：", UVR)

# IVB指标计算
male_comment_score_sum = commented.loc[
    commented["gender_code"] == 1, "comment"
].sum()
female_comment_score_sum = commented.loc[
    commented["gender_code"] == 2, "comment"
].sum()

female_comment_score_avg = female_comment_score_sum / number_female_comment
male_comment_score_avg = male_comment_score_sum / number_male_comment

IVB = abs(male_comment_score_avg - female_comment_score_avg)
print("IVB指标：", IVB)


                                                
