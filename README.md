# Paper Phone

A printable Paper Phone which helps you take a break away from your digital world

![Paper Phone Image](190807PaperPhoneStills295-Edit.jpg)

A lot of people feel that they spend too much time on their phones and struggle to find a balance with technology.

Paper Phone helps you have a little break away from your digital world by printing a personal booklet of the key information you’ll need that day.

An app lets you choose what to include such as favourite contacts, maps and meetings and then prints them directly to a sheet of paper.

Customisable “paper apps” like recipes, phrasebooks and notepads let you get things done or unwind in a more focussed way.

We hope this little experiment can help you try a digital detox from technology and help you focus on the things that matter the most.

Paper Phone is an experimental open source Android app which is available to try right now. All of the code is available on Github for people to play with and hopefully adapt and evolve!

Services:
* [OpenWeather](https://openweathermap.org/api)
* [Directions](https://developers.google.com/maps/documentation/directions/start)
* [Static Maps](https://developers.google.com/maps/documentation/maps-static/intro)
* [Places](https://developers.google.com/places/web-service/intro)
* [Tasks](https://developers.google.com/tasks/)

Android APIs (legacy):
* [PdfRenderer](https://developer.android.com/reference/android/graphics/pdf/PdfRenderer) - Rendering PDFs
* [ContentProvider](https://developer.android.com/guide/topics/providers/content-providers) - Read local contacts and calendar events
* [Android Printing Framework](https://developer.android.com/training/printing) - Printing

Google-era services and libraries (OpenWeather, Maps Directions/Static/Places/Tasks, OkHttp, Room, Lottie) are retained by the Android build only; the GNOME version does not use them.

[Original Android app on Google Play](https://play.google.com/store/apps/details?id=com.withgoogle.experiments.unplugged)

[Read more on Experiments with Google](https://experiments.withgoogle.com/paper-phone)

[Find out even more on our site](http://specialprojects.studio/project/paper-phone)

---

## GNOME版（進行中）

Google Maps・OpenWeatherへの依存を[ローカルOSMデータ](docs/offline-maps.md)と[Norwegian Meteorological Institute](https://api.met.no/)へ置き換えるGNOME版の構築は進行中である。製品用Flatpakはまだ無い。

- **位置**: 表示許可のポータル1回だけ取得し、47都道府県の判定は内蔵の一般化境界を用いたローカル処理。精度の上限は設けず、非有限・範囲外・不正値のみ除外する。
- **地図データ**: [movisda](https://osm.download.movisda.io/)のOSM PBFを都道府県別に明示取得・更新し、ETag/Last-Modifiedで版を節約する。保存先は`$XDG_CACHE_HOME/paperphone/maps`。保存済みの抽出は自動で読み込み、更新だけはボタンで明示的に行う。読込には進捗バーが付き、設定からは地図データを削除できる。
- **地名検索・徒歩経路・印刷地図**: pyosmium + Shapely + RapidFuzzによるローカル検索と制限付きDijkstra。描画はprivacy/local-first無外部サービス、配色は[OSM Liberty](https://github.com/maputnik/osm-liberty)を参考に紙の背景へ調整。紙面に出典（© OpenStreetMap contributors · ODbL · OSM Liberty/OpenMapTiles/OSM Bright）を必ず含める。
- **天気**: [MET Norway Locationforecast](https://api.met.no/)へ有効な座標を1回だけ送る。位置と応答をメモリ内だけで保持し、富化・保存しない。クライアント連絡先はUser-Agentとして自分で入力する。画面には「位置情報と連絡先をMET Norwayへ送信します。」だけを説明として示し、単一の権利表示「MET Norway · CC-BY 4.0」で出典とライセンスの参照先を1行にまとめて紙面に載せる。
- **連絡先・予定・TODO・情報ソース**: Evolution Data Serverをread-onlyに読む。GNOME Online AccountsまたはEvolutionでの事前構成が必要。アプリは起動時に自動でソースを列挙し、行の右に「成功」「失敗」の短い状態語を出す。連絡先・予定・TODO・情報ソースはそれぞれ別のページに分け、各行には種別名を繰り返さず見出しでまとめる。
- **PDF保存・印刷**: 生成済みの同じPDFでプレビュー・保存・印刷する。A4横・100% Actual Size・用紙1枚にPDF1ページで3回半分に折る。折り方の案内は折り方そのものを取り扱い、両面の綴じ方向や印刷結果の見直しといった免責的な指示を含まない。
- **地名検索**: 出発地・目的地の検索は入力と並行して候補を出し、選択した候補の名称だけを表示する。検索や取り消しのための専用ボタンは置かない。
- **設定の保存**: 言語・表紙の名前・MET Norway用の連絡先・選択中のソースと項目・紙アプリの選択・地図データの選択県を開いたときに戻せるように保存する。写真の実データなど個人の内容は保存しない。

### 開発環境

```sh
python3 -m venv --system-site-packages .venv-gnome
.venv-gnome/bin/python -m pip install -r requirements.txt 'meson>=1.6,<2'
export PATH="$PWD/.venv-gnome/bin:$PATH"
meson setup build/gnome
meson compile -C build/gnome
meson test -C build/gnome --print-errorlogs
meson install -C build/gnome --destdir build/stage
```

実際のGUI起動（`meson run`相当）は、実ソース・実位置・実プリンターを触らない環境に限る。手動確認は[docs/gnome-dependencies.md](docs/gnome-dependencies.md)を参照。

### 現在の制限

- 配布用アプリID・MET Norwayクライアント連絡先は未確定。
- Flatpak manifest・アイコンは未作成。
- 宅内プリンターでの実紙出力・摺り畳み一致は未検証。
- Android/Kotlin版は本移行が完了するまで残す。
