# inventory-tracker

A dummy inventory management CLI used as a test target for the [agentic-sdlc](../agentic-sdlc) project.

Any commits pushed from this repo trigger the asdlc webhook pipeline, which runs code review, security, and performance agents against the diff.

## Purpose

This repo exists solely to generate real git push events for testing asdlc workflows. It is not a real application.

## Setup

The `pre-push` hook is already installed at `.git/hooks/pre-push`. It fires a POST to `http://localhost:8080/git/push` on every push.

To enable HMAC signature validation, set the secret before pushing:

```sh
export GIT_WEBHOOK_SECRET=your-secret-here
git push origin main
```

## Running a test push

```sh
# Make any change, commit, and push
echo "# test" >> notes.txt
git add notes.txt && git commit -m "test: trigger asdlc"
git push origin main
```
