param(
    [string]$Out = "repo-visual-archive"
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path "$Out\snapshots" | Out-Null
New-Item -ItemType Directory -Force -Path "$Out\diffs" | Out-Null
New-Item -ItemType Directory -Force -Path "$Out\bundle" | Out-Null

git fetch --all --tags --prune

git bundle create "$Out\bundle\full-history.bundle" --all

$timeline = "$Out\timeline.tsv"
$html = "$Out\index.html"

"number`ttype`tshort_hash`tfull_hash`tdate`tauthor`tsubject`tparents`tfiles_changed`tinsertions`tdeletions`tsnapshot`tdiff" |
    Set-Content -Encoding UTF8 $timeline

@"
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Git Visual History Archive</title>
  <style>
    body {
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 40px;
      background: #f6f6f6;
      color: #111;
    }
    h1 { margin-bottom: 0; }
    .sub { color: #555; margin-top: 6px; margin-bottom: 28px; }
    .commit {
      background: white;
      border: 1px solid #ddd;
      border-radius: 14px;
      padding: 18px 20px;
      margin-bottom: 14px;
      box-shadow: 0 1px 3px rgba(0,0,0,.04);
    }
    .top {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: baseline;
    }
    .subject {
      font-size: 18px;
      font-weight: 700;
    }
    .type {
      font-size: 12px;
      padding: 4px 8px;
      border-radius: 999px;
      background: #eee;
      white-space: nowrap;
    }
    .merge { background: #dbeafe; }
    .commitType { background: #e5e7eb; }
    .meta {
      color: #555;
      font-size: 13px;
      margin-top: 8px;
      line-height: 1.5;
    }
    .stats {
      margin-top: 10px;
      font-size: 13px;
    }
    .links {
      margin-top: 12px;
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    a {
      color: #0645ad;
      text-decoration: none;
      font-weight: 600;
    }
    a:hover { text-decoration: underline; }
    code {
      background: #f1f1f1;
      padding: 2px 5px;
      border-radius: 5px;
    }
  </style>
</head>
<body>
<h1>Git Visual History Archive</h1>
<div class="sub">Every commit exported as a ZIP snapshot, with matching diff files.</div>
"@ | Set-Content -Encoding UTF8 $html

$commits = git rev-list --reverse --topo-order --all
$i = 0

foreach ($commit in $commits) {
    $i++
    $num = "{0:D4}" -f $i

    $short = git rev-parse --short $commit
    $date = git show -s --format=%cs $commit
    $datetime = git show -s --format=%ci $commit
    $author = git show -s "--format=%an <%ae>" $commit
    $subject = git show -s --format=%s $commit
    $parents = git show -s --format=%P $commit

    $parentCount = 0
    if ($parents.Trim().Length -gt 0) {
        $parentCount = ($parents -split "\s+").Count
    }

    if ($parentCount -gt 1) {
        $type = "merge"
        $typeClass = "merge"
    } else {
        $type = "commit"
        $typeClass = "commitType"
    }

    $safeSubject = $subject.ToLower()
    $safeSubject = $safeSubject -replace "[^a-z0-9_.-]+", "-"
    $safeSubject = $safeSubject.Trim("-")

    if ($safeSubject.Length -gt 70) {
        $safeSubject = $safeSubject.Substring(0, 70)
    }

    if ([string]::IsNullOrWhiteSpace($safeSubject)) {
        $safeSubject = "no-message"
    }

    $snapshotFile = "${num}_${date}_${short}_${type}_${safeSubject}.zip"
    $diffFile = "${num}_${short}.patch"

    $snapshotPath = "snapshots/$snapshotFile"
    $diffPath = "diffs/$diffFile"

    git archive --format=zip --output="$Out\snapshots\$snapshotFile" $commit

    git show -m --stat --patch --format=fuller $commit |
        Set-Content -Encoding UTF8 "$Out\diffs\$diffFile"

    $shortstat = git show --shortstat --format="" $commit |
        Where-Object { $_.Trim().Length -gt 0 } |
        Select-Object -Last 1

    $filesChanged = 0
    $insertions = 0
    $deletions = 0

    if ($shortstat -match "(\d+) files? changed") {
        $filesChanged = [int]$Matches[1]
    }

    if ($shortstat -match "(\d+) insertions?") {
        $insertions = [int]$Matches[1]
    }

    if ($shortstat -match "(\d+) deletions?") {
        $deletions = [int]$Matches[1]
    }

    "$num`t$type`t$short`t$commit`t$datetime`t$author`t$subject`t$parents`t$filesChanged`t$insertions`t$deletions`t$snapshotPath`t$diffPath" |
        Add-Content -Encoding UTF8 $timeline

    $escSubject = [System.Net.WebUtility]::HtmlEncode($subject)
    $escAuthor = [System.Net.WebUtility]::HtmlEncode($author)
    $escParents = [System.Net.WebUtility]::HtmlEncode($parents)

@"
<div class="commit">
  <div class="top">
    <div class="subject">$num. $escSubject</div>
    <div class="type $typeClass">$type</div>
  </div>
  <div class="meta">
    <div><strong>Commit:</strong> <code>$short</code></div>
    <div><strong>Date:</strong> $datetime</div>
    <div><strong>Author:</strong> $escAuthor</div>
    <div><strong>Parents:</strong> <code>$escParents</code></div>
  </div>
  <div class="stats">
    <strong>Stats:</strong> $filesChanged files changed, +$insertions / -$deletions
  </div>
  <div class="links">
    <a href="$snapshotPath">Download snapshot ZIP</a>
    <a href="$diffPath">View diff patch</a>
  </div>
</div>
"@ | Add-Content -Encoding UTF8 $html
}

@"
</body>
</html>
"@ | Add-Content -Encoding UTF8 $html

Write-Host "Done."
Write-Host "Archive created at: $Out"
Write-Host "Open: $Out\index.html"