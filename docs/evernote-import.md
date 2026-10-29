# Moving documents from Evernote

Tabularium imports Evernote exports (`.enex` files). There are two steps: export from
Evernote, then import in Tabularium. Nothing is uploaded anywhere. Evernote itself is not
changed.

## 1. Export from Evernote

Evernote Web cannot export, so use one of these two routes.

### A. The whole account with evernote-backup (recommended for many notebooks)

[evernote-backup](https://github.com/vzhd1701/evernote-backup) is a free, open-source tool. It
downloads every note into a local database and writes the ENEX files from there.

1. Install it in PowerShell:

   ```powershell
   winget install -e --id vzhd1701.EvernoteBackup
   ```

   Open a new PowerShell window afterwards, so the command is found. If Windows Smart App
   Control blocks the program, use route B instead.

2. Make an empty working folder and go into it:

   ```powershell
   mkdir $HOME\EvernoteBackup; cd $HOME\EvernoteBackup
   ```

3. Sign in. Choose one of the two commands:

   ```powershell
   evernote-backup init-db --oauth-method import
   ```

   This reuses the sign-in of the Evernote desktop app on this computer. The desktop app must
   be installed and signed in, and it must stay signed in until the export is done. It avoids
   the free plan's one-session limit.

   Or sign in through the browser:

   ```powershell
   evernote-backup init-db
   ```

   On the free plan Evernote allows one active session, so this may sign out your desktop or
   phone app.

4. Download everything. This can take a while. If it stops, run it again and it continues
   where it left off.

   ```powershell
   evernote-backup sync
   ```

5. Write one `.enex` file per note, in one folder per notebook:

   ```powershell
   evernote-backup export --single-notes Export
   ```

### B. Notebook by notebook, from the Evernote desktop app

Right-click a notebook, choose **Export notebook…**, pick the **ENEX** format, and save the
file under the notebook's name, for example `Invoices.enex`. The file name becomes the
notebook name in Tabularium. Repeat this for each notebook.

Exporting a whole notebook has no size limit. Exporting a selection of notes is limited to
100 notes at a time.

Use one route for the whole move. The two routes may write the same note slightly
differently, and Tabularium would then import it twice (attachments are still recognised as
the same files).

## 2. Import in Tabularium

1. Copy the exported files into the ingest folder. Folders are fine, for example the whole
   `Export` folder from route A. A normal scan leaves `.enex` files alone, so nothing is
   imported by surprise.
2. Open **Maintenance → Import from Evernote** and click **Preview**. It counts the notes and
   shows what they will become, without changing anything.
3. Click **Import**. Progress is shown while it runs. When it finishes, the new documents are
   in **To be tagged**.

## What a note becomes

| In the note | In Tabularium |
|---|---|
| Photos (JPEG, PNG, GIF, …) | One PDF with one page per photo, in the order of the note. JPEG photos are stored as they are, without re-compression. Phone photos are turned upright. |
| A PDF or any other attachment | A document of its own, with the file unchanged. Formats that cannot go into a PDF, such as HEIC photos, are kept as they are. |
| No attachments, only text | The note printed to an A4 PDF by Microsoft Edge. |
| Several documents from one note | Linked to each other as *related*. |
| Title | The document title. Several documents from one note get the attachment's name added. |
| Notebook, Evernote tags, text | Written into the document's note, e.g. `From Evernote. Notebook: Invoices. Tags: FV, 2021.`, followed by the note's text. |
| Created/updated dates | Not used. Set the dates while tagging. |
| Empty notes | Skipped. |

Each `.enex` file moves to `_processed` once its notes are imported. If any of its notes
fails, the file moves to `_failed` instead, and the reason is listed under Maintenance and in
the log.

## Importing again

Every imported note is remembered. Importing the same export again, or a later export of the
same account, adds only the notes that are new. A note edited in Evernote since the last
import counts as new and comes in again as new documents; the old ones stay. A file that is already in the
archive is not stored twice. If it belongs to a newly imported note, the new documents are
linked to it. So it is safe to fix a problem, move a file back from `_failed` into the ingest
folder, and import again.

Documents that were outdated in Tabularium are not brought back by a later import.
