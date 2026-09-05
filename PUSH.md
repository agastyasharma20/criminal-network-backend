# Pushing this repo

The remote is already configured. From inside this directory:

    git push -u origin main

If GitHub asks for a password, it wants a personal access token, not your
account password: github.com/settings/tokens -> generate a classic token with
`repo` scope, and paste that as the password.

Or with the GitHub CLI:

    gh auth login
    git push -u origin main

## What is in the commit

One commit, 29 files: the backend pipeline, unit tests, the synthetic dataset,
and the ground-truth answer key. Nothing is gitignored except *.db, __pycache__
and .env.
