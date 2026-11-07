#!/usr/bin/env bash
set -euo pipefail

reference_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
candidate_pdf="${1:?候補PDFのパスを指定してください}"
output_dir="${2:-"$reference_dir/comparison-output"}"

for tool in pdftoppm pdfinfo compare identify; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf '必要なコマンドがありません: %s\n' "$tool" >&2
        exit 2
    fi
done

if [[ ! -f "$candidate_pdf" ]]; then
    printf '候補PDFがありません: %s\n' "$candidate_pdf" >&2
    exit 2
fi

page_count="$(pdfinfo "$candidate_pdf" | awk '/^Pages:/ {print $2}')"
if [[ -z "$page_count" || "$page_count" -lt 2 ]]; then
    printf '候補PDFには地図ページを含む2ページ以上が必要です（ページ数: %s）。\n' "${page_count:-不明}" >&2
    exit 2
fi

mkdir -p "$output_dir"
temporary_dir="$(mktemp -d)"
trap 'rm -rf "$temporary_dir"' EXIT

pdftoppm -f 1 -l 2 -r 150 -png "$reference_dir/coordinate-reference.pdf" "$temporary_dir/reference-page" >/dev/null 2>&1
pdftoppm -f 1 -l 2 -r 150 -png "$candidate_pdf" "$output_dir/candidate-page" >/dev/null 2>&1

for page in 1 2; do
    reference_png="$temporary_dir/reference-page-$page.png"
    candidate_png="$output_dir/candidate-page-$page.png"
    difference_png="$output_dir/diff-page-$page.png"

    reference_size="$(identify -format '%wx%h' "$reference_png")"
    candidate_size="$(identify -format '%wx%h' "$candidate_png")"
    if [[ "$reference_size" != "$candidate_size" ]]; then
        printf 'ページ%sの画像寸法が違います（参照 %s / 候補 %s）。\n' "$page" "$reference_size" "$candidate_size" >&2
        exit 1
    fi

    set +e
    metric="$(compare -metric AE "$reference_png" "$candidate_png" "$difference_png" 2>&1)"
    compare_status=$?
    set -e
    if [[ "$compare_status" -gt 1 ]]; then
        printf 'ページ%sの比較に失敗しました: %s\n' "$page" "$metric" >&2
        exit "$compare_status"
    fi
    printf '%s\n' "$metric" > "$output_dir/page-$page-ae.txt"
    printf 'ページ%s: AE=%s（差分画像: %s）\n' "$page" "$metric" "$difference_png"
done
