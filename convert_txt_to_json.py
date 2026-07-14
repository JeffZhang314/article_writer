import re
import json
with open("example_articles.txt", "r", encoding="utf-8") as file:
    content = file.read()
indices = [match.start() for match in re.finditer("文章 ", content)]
pairs = [[0, 0] for _ in indices]
for i in range(len(indices)):
    if i > 0:
        if i + 1 < 10:
            pairs[i - 1][1] = indices[i] - 4
            pairs[i][0] = indices[i] + 7
        else:
            pairs[i - 1][1] = indices[i] - 4
            pairs[i][0] = indices[i] + 8
pairs[0][0] = 7
pairs[len(pairs) - 1][1] = len(content) - 4
articles = [content[i[0]:i[1] + 1] for i in pairs]
output = ""
for i in articles:
    output += "CHINESE WECHAT ARTICLE THAT WAS ALREADY POSTED BY THE ACCOUNT STARTS HERE [\n\n"
    output += i
    output += "\n\n] CHINESE WECHAT ARTICLE THAT WAS ALREADY POSTED BY THE ACCOUNT ENDS HERE\n\n"
output = output[:-2]
with open("cleaned_example_articles.txt", "w", encoding="utf-8") as file:
    file.write(output)
#with open("example_articles.json", "w", encoding="utf-8") as json_file:
#    json.dump(articles, json_file, ensure_ascii=False, indent=4)