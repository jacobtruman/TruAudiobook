import base64
import json
import os

from tru_audiobook import TruAudiobook
from bs4 import BeautifulSoup

# list of spine urls, collected one at a time, unfortunately
# urls = [
#     "https://dewey-c140f6f4a91768f73ee2755a8cd11c32.listen.libbyapp.com/%7B2A0FC1B8-0F18-4F23-AC00-3998A787DA64%7DFmt425-Part01.mp3?cmpt=eyJzcGluZSI6MH0%3D--e01e25e3f5dc1789b702c7122b573373fea79df8",
#     "https://dewey-c140f6f4a91768f73ee2755a8cd11c32.listen.libbyapp.com/%7B2A0FC1B8-0F18-4F23-AC00-3998A787DA64%7DFmt425-Part02.mp3?cmpt=eyJzcGluZSI6MX0%3D--f2dcc17408b8266074bbd0c0da64763a36d479f2",
# ]
urls = os.environ.get('BOOK_URLS').split(',')

# table of contents html (ul element)
toc_html = f"""{os.environ.get('BOOK_TOC')}"""

# the window.bData object from the page under "Developer Tools > Network" like this:
# https://dewey-{BOOK ID STRING}.listen.libbyapp.com/?m={LONG BASE 64 ENCODED STRING}&s={SOME ID STRING}&p=lib-315
data = json.loads(f"""{os.environ.get('BOOK_DATA')}""")
# and window.tData
tdata = json.loads(f"""{os.environ.get('BOOK_TDATA')}""")

title_dict = data.get('title')
title = title_dict.get('main')
search_title = title_dict.get('search', title)

clean_title = TruAudiobook.clean_string(title, [("'", "")]).replace(" ", "_").lower()
book_data_file = TruAudiobook.resolve_path(f'./book_data/{clean_title}.json')
data_dir = os.path.dirname(book_data_file)
if not os.path.isdir(data_dir):
    print(f'Creating directory {data_dir}')
    os.mkdir(data_dir)

crid = data["-odread-crid"][0].upper()

fields = ['title', 'creator', 'nav', 'spine', '-odread-buid', '-odread-bonafides-d', '-odread-crid']

toc = []
spine = []

parsed_html = BeautifulSoup(toc_html, features="html.parser")
for row in parsed_html.find_all('li', attrs={'class': 'chapter-dialog-row'}):
    title = row.find('div', attrs={'class': 'chapter-dialog-row-title'}).text
    timestamp = row.find('span', attrs={'class': 'place-phrase-visual'}).text
    toc.append({"title": title, "timestamp": timestamp})

for index, url in enumerate(urls):
    spine_index = base64.b64encode("{{\"spine\":{index}}}".format(index=index).encode()).decode()
    path_thing = f"{{{crid}}}Fmt425-Part{str((index + 1)).zfill(2)}.mp3?cmpt={spine_index}--{url.split('--')[-1]}"
    spine.append({
        "id": url.split('--')[-1]
    })

data["nav"]["toc"] = toc
data["spine"] = spine

result = {}
for key, value in data.items():
    if key in fields:
        result[key] = value

result["coverUrl"] = tdata.get("codex").get("title").get("cover").get("imageURL")

if os.path.isfile(book_data_file):
    print(f"File already exists: {book_data_file}")
    exit()
with open(book_data_file, 'w') as file_handle:
    print(f"Writing to {book_data_file}")
    json.dump(result, file_handle, indent=2)
