# 匿名化した座標・素材ベースの比較資料

## Android版PDFを直接取得できない理由

この作業環境ではAndroid SDKの有効なディレクトリを確認できず、`adb` と `emulator` も利用できません。さらにリポジトリ内に既存PDFはありません。Android版を実行して匿名化PDFを出力する手段がないため、Androidの実レンダリング結果を基準PDFとして保存したものではありません。

代替資料として、`PdfGenerator.kt`、`HomeActivity.kt`、各紙面モジュールの座標・配列順・回転、および既存の「Origami Pigeon」素材を使った座標参照PDFと、そのラスター画像を保存しています。連絡先・予定・現在地は架空の値または抽象図であり、地図は架空でナビゲーションに使用できません。

**制約:** PDF内の薄い破線区画、サンプル文字、写真枠、架空地図、書体は比較用の模式表現で、Android版の実際の描画・文字詰め・地図画像・印刷結果を再現しません。したがってピクセル一致の判定には使えません。GNOME版の配置、区画、回転、追加地図ページと折り目位置の目視・ラスター差分を始めるための代替基準です。Android端末・エミュレーターが利用可能になった場合は、実PDFに置き換える必要があります。

## 保存物

- `coordinate-reference.pdf`: A4横・2ページの座標参照PDF。1ページ目は8区画、2ページ目は追加地図ページの模式図。
- `coordinate-reference-page-1.png` / `coordinate-reference-page-2.png`: 上記PDFを150dpiでラスター化した比較画像。
- `render_coordinate_reference.py`: 既存ソースの座標と画像素材からPDFを再生成するスクリプト。
- `compare_reference.sh`: 候補PDFの先頭2ページを同じ150dpiでラスター化し、ImageMagickで差分画像とAE画素数を出すスクリプト。

## 再生成・比較手順

必要ツールはPython 3、PyCairo、Popplerの`pdftoppm`/`pdfinfo`、ImageMagickの`compare`/`identify`です。検証した環境ではPyCairo 1.28.0 / Cairo 1.18.6を利用しました。

```sh
cd openspec/changes/migrate-to-gnome/reference
python3 render_coordinate_reference.py
pdftoppm -f 1 -l 2 -r 150 -png coordinate-reference.pdf coordinate-reference-page
pdfinfo coordinate-reference.pdf
bash compare_reference.sh /path/to/candidate.pdf ./comparison-output
```

比較出力先に`candidate-page-1.png`、`candidate-page-2.png`、`diff-page-1.png`、`diff-page-2.png`と`page-*-ae.txt`が生成されます。AEは異なる画素数であり、この模式図との差が大きいこと自体を不具合とは判定しません。候補PDFとページ寸法を揃え、生成画像を目視して配置・向き・区画・折り目を確認してください。素材・文字内容・レンダラーが異なる箇所は期待される差分として扱います。
