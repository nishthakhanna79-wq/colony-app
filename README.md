# Colony

A mouse colony tracker built with Streamlit. Everyone in the lab signs in with one shared password. This repo holds only code. Records and gel files live in a separate private GitHub repo, so they stay private and survive Streamlit restarts.

## How it fits together

| Piece | Where it lives | What it holds |
|---|---|---|
| App code | This public repo | No records and no passwords |
| Colony data | `data` branch of a private repo | `mice.csv`, `pairs.csv`, `litters.csv`, `gels.csv`, `tumors.csv`, `settings.csv`, `gels/` |
| Running app | Streamlit Community Cloud, as a public app | Keeps nothing permanently |
| Secrets | Streamlit's Secrets box | The shared password and the GitHub token |

Anyone can open the app's URL, but they see only a sign-in form until they log in. Nothing is read from the private repo before that.

This app uses its own password check, not Streamlit's `st.login`. On Community Cloud an app with an `[auth]` section in its secrets counts as your one private app.

## Set it up

### 1. Make a private data repo

Create a private GitHub repo with a branch named `data`. It must stay private.

### 2. Make an access token

On GitHub, go to **Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new token**.

- **Repository access:** Only select repositories, then your data repo
- **Permissions:** Contents, Read and write
- **Expiration:** Pick a date and put a renewal reminder on your calendar. The app can't save or load once the token expires.

### 3. Deploy

1. On [share.streamlit.io](https://share.streamlit.io), click **Create app** and choose this repo, branch `main`, main file `streamlit_app.py`.
2. Open **Advanced settings** and paste `.streamlit/secrets.toml.example` into **Secrets**, with your real values.
3. Deploy.

### 4. Check it

- Open the app in a private window. You should see only the Colony name and a sign-in form.
- Try a wrong password. It should refuse you.
- Sign in, add a test mouse, and check that `mice.csv` appears on the `data` branch of your private repo.
- Delete the test mouse.

## Day to day

- **Add a lab member:** Give them the shared password.
- **Remove a lab member:** Change `password` under `[login]` in the app's Secrets, then give the new one to everyone else. There's no way to remove one person alone.
- **Signing in:** People stay signed in until they refresh, or the phone drops the connection for a while, and for 12 hours at most. Saving the password in the phone's browser makes signing in quick.
- **Failed sign-ins:** After 20 wrong tries in 15 minutes, sign-in pauses for everyone for up to 15 minutes.
- **See what changed:** Settings has a link to the `data` branch history. Each save is one commit. With a shared login, commits say `lab` rather than naming a person.
- **Undo a mistake:** Revert the commit on GitHub. The app picks up the change within about 20 seconds.
- **Edit data by hand:** You can edit the CSV files on GitHub, but keep the column names. If a file can't be read, the app shows an error and refuses to save over it.

## Genotypes

Every mouse has 3 alleles, always shown in this order: Cre / p53 / Brca2. For example, `P / het / het`.

| Allele | Values |
|---|---|
| Cre | P, N, TBD |
| p53 | hom, het, WT, TBD |
| Brca2 | hom, het, WT, TBD |

TBD means not typed yet. Don't rename it to anything containing the letters of another value. The grid's dropdown filters on what you type, and with "pending" as the label, typing P then Enter saved the wrong value.

## Limits

- **Size:** GitHub recommends keeping a repo under 1 GB. Gel photos are shrunk to 2000 pixels on the longest side, and location data is removed.
- **Backups:** GitHub is the only copy. To keep another, clone the `data` branch anywhere.
- **Sleep:** Community Cloud puts an app to sleep after 12 hours without visitors. The next visitor can wake it.
- **Not in this version:** Editing or deleting a gel record in the app, spreadsheet import, and AI gel calling.

## Run it locally

Local mode uses a folder of made-up data and no login. It turns itself off as soon as `[auth]` or `[github]` secrets exist.

```bash
pip install -r requirements-dev.txt
python tests/seed.py /tmp/colony-data
COLONY_DEV_USER=yourname COLONY_LOCAL_DATA=/tmp/colony-data streamlit run streamlit_app.py
```

## Tests

```bash
python -m pytest tests            # Data, genetics, GitHub stand-in, login rules
python tests/e2e_login.py         # The login gate in a real browser, with a stand-in for GitHub
```
