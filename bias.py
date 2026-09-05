import json
from pathlib import Path
import pandas as pd

# 首先，获取当前脚本所在的目录路径
BASE_DIR = Path(__file__).resolve().parent

# 开始读文件
with (BASE_DIR /"dataset" /"user_info.jsonl").open("r", encoding = "utf-8") as f:
    userdata = json.load(f)
with (BASE_DIR /"dataset" /"course_info.jsonl").open("r", encoding = "utf-8") as f:
    coursedata = json.load(f)
with (BASE_DIR /"dataset" /"user_course_interactions").open("r", encoding = "utf-8") as f:
    interactiondata = json.load(f)

# 用pandas dataframe格式读取数据
df_user = pd.DataFrame(userdata)
df_course = pd.DataFrame(coursedata)
df_interaction = pd.DataFrame(interactiondata)


### 合并三个表，以及处理男女用户信息

# 把用户表和交互表按照user_id进行合并
df_user_interaction = pd.merge(df_user, df_interaction, on = "user_id", how = "inner")

# 把df_user_interaction和课程表按照course_id进行合并
df_user_course_interaction = pd.merge(df_user_interaction, df_course, on = "course_id", how = "inner")

# 统计男女用户数量
number_male = df_user[df_user["gender_code"] == 1]["user_id"].nunique()
number_female = df_user[df_user["gender_code"] == 2]["user_id"].nunique()

if number_male == 0 or number_female == 0:
    print("Error: One of the gender groups has zero users. Cannot compute bias.")

print(number_male, number_female)

# 计算UVR和IVB两个指标

# UVR指标计算 
number_male_comment = df_user_course_interaction[(df_user_course_interaction["gender_code"] == 1) & (df_user_course_interaction["comment_score"] != 0)]["user_id"].nunique()
number_female_comment = df_user_course_interaction[(df_user_course_interaction["gender_code"] == 2) & (df_user_course_interaction["comment_score"] != 0)]["user_id"].nunique()

print("男性的评论用户数：", number_male_comment,"女性的评论用户数：", number_female_comment)

P_female = number_female_comment / number_female
P_male = number_male_comment / number_male

UPR = P_female / P_male
print("UVR指标：", UPR)

# IVB指标计算
female_comment_score_sum = 0
male_comment_score_sum = 0
for interaction in df_user_course_interaction:
    if interaction["gender_code"] == 1:
        male_comment_score_sum += interaction["comment_score"]
    elif interaction["gender_code"] ==2:
        female_comment_score_sum += interaction["comment_score"]

female_comment_score_avg = female_comment_score_sum / number_female_comment
male_comment_score_avg = male_comment_score_sum / number_male_comment

IVB = abs(male_comment_score_avg - female_comment_score_avg)
print("IVB指标：", IVB)


                                                
