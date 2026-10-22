<!-- p1 -->
Web Applications
Introduction to Git
Suhel Hammoud
<!-- p2 -->
██ Git Tutorial Overview
   •  How to import projects into Git
   •  How to commit changes
   •  How to manage branches
   •  How to collaborate using Git
2 / 29
<!-- p3 -->
██ Getting Help in Git
$ man git-log
$ git help log
   •  man shows the manual page
   •  git help allows using any viewer (see git-help[1])
3 / 29
<!-- p4 -->
██ Setting up Your Identity
   •  Configures your Git username and email globally.
$ git config --global user.name "Your Name"
$ git config --global user.email "you@yourdomain.example.com"
4 / 29
<!-- p5 -->
██ Importing a New Project
$ tar xzf project.tar.gz
$ cd project
$ git init
Initialized empty Git repository in .git/
   •  Project is now versioned under Git.
5 / 29
<!-- p6 -->
██ Making changes
Modify some files, then add their updated contents to the index:
$ git add file1 file2 file3
You can see what is about to be committed using git diff with the --cached option:
$ git diff --cached
Without --cached, git diff will show you any changes that you’ve made but not yet added to the index.
6 / 29
<!-- p7 -->
██ Status
You can also get a brief summary of the situation with git status:
$ git status
On branch main
Changes to be committed:
  (use "git restore --staged <file>..." to unstage)
modified:   file1
modified:   file2
modified:   file3
7 / 29
<!-- p8 -->
██ Commit Changes
   •  Commit your changes with:
$ git commit
   •  Will again prompt you for a message describing the change, and then record a new version of the
      project.
   •  Alternatively, instead of running git add beforehand, you can use
$ git commit -a
8 / 29
<!-- p9 -->
██ Good Commit Messages
   •  Start with a short title (≤50 characters)
   •  Then a blank line
   •  Then a detailed description
Example:
Fix login bug
Fixed session timeout issue causing user logout.
Added refresh token support.
9 / 29
<!-- p10 -->
██ Git Tracks Content, Not Files
   •  git add stages new and modified contents.
   •  Git does not track "file renames" explicitly — only content.
██ Ignoring Files
   •  Use .gitignore to tell Git which files/folders to ignore.
# Example .gitignore
*.log
node_modules/
.env
10 / 29
<!-- p11 -->
██ Viewing Project History
$ git log
commit 89abcde...
Author: Your Name <you@example.com>
Date:   Thu Apr 29 12:00 2025
Updated file1, file2, file3
commit 0123456...
Author: Your Name <you@example.com>
Date:   Thu Apr 29 11:00 2025
Initial commit
11 / 29
<!-- p12 -->
██ Managing Branches
$ git branch experimental
$ git branch
* main
experimental
$ git switch experimental
Switched to branch 'experimental'
   •  Alternatively, use git checkout -b to create a new branch.
$ git checkout -b experimental
Switched to a new branch 'experimental'
12 / 29
<!-- p13 -->
██ Merging Changes
   •  Merged development lines into main.
$ git checkout main
$ git merge experimental
2 files changed, 2 insertions(+), 1 deletion(-)
13 / 29
<!-- p14 -->
██ Resolving Merge Conflicts
   •  Conflicts occur when two branches modify the same part of a file.
   •  Git will mark conflicts in the file.
   •  Manually edit the file to resolve conflicts.
   •  After fixing:
git add <file>
git commit
14 / 29
<!-- p15 -->
██ Deleting Branches
$ git branch -d experimental
Deleted branch experimental (was 89abcde).
$ git branch -D crazy-idea
Deleted branch crazy-idea (was deadbeef).
15 / 29
<!-- p16 -->
██ Using Git for Collaboration
   •  Bob clones Alice's repository.
   •  Alice pulls Bob's changes.
bob$ git clone 'url_to_alice_repo' myrepo
alice$ git pull 'url_to_bob_repo' main
Cloning into 'myrepo'...
done.
   •  Bob makes changes.
bob$ git commit -a
[main 89abcde] Bob's changes
   •  Bob pushes his changes to online
      repository.
bob$ git push origin main
16 / 29
<!-- p17 -->
██ Inspecting Remote Changes
$ git fetch https://github.com/bob/repo.git main
$ git log -p HEAD..FETCH_HEAD
   •  Review remote changes before merging.
██ Defining Remote Shortcuts
$ git remote add bob https://github.com/bob/repo.git
$ git fetch bob
   •  Simplify repetitive fetches and pulls.
17 / 29
<!-- p18 -->
██ Pulling from Remote Branch
$ git pull . remotes/bob/main
   •  Merge remote-tracked changes into the local branch.
   •  pull = fetch + merge.
18 / 29
<!-- p19 -->
██ Exploring History
$ git show HEAD
commit 89abcde...
$ git show HEAD^
commit 0123456...
$ git show HEAD~4
   •  Traverse commit ancestry.
19 / 29
<!-- p20 -->
██ Tagging Commits
$ git tag v2.5 1b2e1d63ff
   •  Mark commits for releases and stable points.
██ Diffing Commits
$ git diff v2.5 HEAD
$ git branch stable v2.5
   •  View differences and create stable branches.
   •  Start a new branch named "stable" based at v2.5
20 / 29
<!-- p21 -->
██ Resetting to Previous Commits
$ git reset --hard HEAD^
HEAD is now at 0123456 Initial commit
   •  Warning: Destroys history after HEAD^.
21 / 29
<!-- p22 -->
██ Searching Content
$ git grep "hello" v2.5
src/hello.c: printf("hello world!\n");
   •  Search for text across your repository.
22 / 29
<!-- p23 -->
██ Visualizing Git History
$ gitk
   •  Shows a full visual graph of branches, commits, merges.
██ Comparing Specific Files
$ git diff v2.5:Makefile HEAD:Makefile.in
$ git show v2.5:Makefile
   •  Compare different versions of individual files.
23 / 29
<!-- p24 -->
██ Distributed Git
   •  Git is Distributed.
   •  You do a "clone" of the entire repository.
   •  Each user can has a full backup of the main repository.
   •  If the server crashes, any user’s copy can restore it.
   •  No single point of failure, unless only one copy exists.
   •  Endless workflows can be implemented.
   •  Flexibility and robustness are core features.
24 / 29
<!-- p25 -->
██ Subversion-Style Workflow
Centralized workflow for Git:
   •  All developers push to the same server.
   •  Git enforces synchronization: you can't
      push unless you've fetched new changes.
25 / 29
<!-- p26 -->
██ Integration Manager Workflow
Popular in open-source projects:
   •  Developers clone the blessed repository.
   •  Push to their own repositories.
   •  Request the Integrator to pull changes.
26 / 29
<!-- p27 -->
██ Dictator and Lieutenants Workflow
Used for large-scale projects (e.g., Linux Kernel):
   •  Lieutenants manage subsystems.
   •  Dictator pulls from lieutenants.
   •  Blessed repository is updated for everyone to clone.
27 / 29
<!-- p28 -->
██ Summary
   •  Git tracks content, not files
   •  Branches and merges are cheap
   •  Collaboration is seamless
   •  Visualization tools (example: gitk) make Git easier to understand
28 / 29
<!-- p29 -->
References
   •  https://git-scm.com
29 / 29
