# Copyright (C) 2012-2021 jrnl contributors
# License: https://www.gnu.org/licenses/gpl-3.0.html

import logging
import sys

from . import install
from . import plugins
from .Journal import open_journal
from .config import get_journal_name
from .config import scope_config
from .config import get_config_path
from .editor import get_text_from_editor
from .editor import get_text_from_stdin
from . import time
from .override import apply_overrides
from .path import expand_path

from jrnl.exception import JrnlException
from jrnl.messages import Message
from jrnl.messages import MsgText
from jrnl.messages import MsgType


def run(args):
    """
    Flow:
    1. Run standalone command if it doesn't require config (help, version, etc), then exit
    2. Load config
    3. Run standalone command if it does require config (encrypt, decrypt, etc), then exit
    4. Load specified journal
    5. Start write mode, or search mode
    6. Profit
    """

    # Run command if possible before config is available
    if callable(args.preconfig_cmd):
        return args.preconfig_cmd(args)

    # Load the config, and extract journal name
    config = install.load_or_install_jrnl(args.config_file_path)
    original_config = config.copy()

    # Apply config overrides
    config = apply_overrides(args, config)

    args = get_journal_name(args, config)
    config = scope_config(config, args.journal_name)

    # Run post-config command now that config is ready
    if callable(args.postconfig_cmd):
        return args.postconfig_cmd(
            args=args, config=config, original_config=original_config
        )

    # --- All the standalone commands are now done --- #

    # Get the journal we're going to be working with
    journal = open_journal(args.journal_name, config)

    kwargs = {
        "args": args,
        "config": config,
        "journal": journal,
    }

    if _is_write_mode(**kwargs):
        write_mode(**kwargs)
    else:
        search_mode(**kwargs)


def _is_write_mode(args, config, **kwargs):
    """Determines if we are in write mode (as opposed to search mode)"""
    write_mode = True

    # Are any search filters present? If so, then search mode.
    write_mode = not any(
        (
            args.contains,
            args.delete,
            args.edit,
            args.change_time,
            args.export,
            args.end_date,
            args.today_in_history,
            args.month,
            args.day,
            args.year,
            args.limit,
            args.on_date,
            args.short,
            args.starred,
            args.start_date,
            args.strict,
            args.tags,
        )
    )

    # Might be writing and want to move to editor part of the way through
    if args.edit and args.text:
        write_mode = True

    # If the text is entirely tags, then we are also searching (not writing)
    if (
        write_mode
        and args.text
        and all(word[0] in config["tagsymbols"] for word in " ".join(args.text).split())
    ):
        write_mode = False

    return write_mode


def write_mode(args, config, journal, **kwargs):
    """
    Gets input from the user to write to the journal
    1. Check for input from cli
    2. Check input being piped in
    3. Open editor if configured (prepopulated with template if available)
    4. Use stdin.read as last resort
    6. Write any found text to journal, or exit
    """
    logging.debug("Write mode: starting")

    if args.text:
        logging.debug("Write mode: cli text detected: %s", args.text)
        raw = " ".join(args.text).strip()
        if args.edit:
            raw = _write_in_editor(config, raw)

    elif not sys.stdin.isatty():
        logging.debug("Write mode: receiving piped text")
        raw = sys.stdin.read()

    else:
        raw = _write_in_editor(config)

    if not raw or raw.isspace():
        logging.error("Write mode: couldn't get raw text or entry was empty")
        raise JrnlException(Message(MsgText.NoTextReceived, MsgType.ERROR))

    logging.debug(
        'Write mode: appending raw text to journal "%s": %s', args.journal_name, raw
    )
    journal.new_entry(raw)
    print(f"[Entry added to {args.journal_name} journal]", file=sys.stderr)
    journal.write()
    logging.debug("Write mode: completed journal.write()", args.journal_name, raw)


def search_mode(args, journal, **kwargs):
    """
    Search for entries in a journal, then either:
    1. Send them to configured editor for user manipulation (and also
       change their timestamps if requested)
    2. Change their timestamps
    2. Delete them (with confirmation for each entry)
    3. Display them (with formatting options)
    """
    kwargs = {
        **kwargs,
        "args": args,
        "journal": journal,
        "old_entries": journal.entries,
    }

    # Filters the journal entries in place
    _search_journal(**kwargs)

    # Where do the search results go?
    if args.edit:
        # If we want to both edit and change time in one action
        if args.change_time:
            # Generate a new list instead of assigning so it won't be
            # modified by _change_time_search_results
            selected_entries = [e for e in journal.entries]

            no_change_time_prompt = len(journal.entries) == 1
            _change_time_search_results(no_prompt=no_change_time_prompt, **kwargs)

            # Re-filter the journal enties (_change_time_search_results
            # puts the filtered entries back); use selected_entries
            # instead of running _search_journal again, because times
            # have changed since the original search
            kwargs["old_entries"] = journal.entries
            journal.entries = selected_entries

        _edit_search_results(**kwargs)

    elif args.change_time:
        _change_time_search_results(**kwargs)

    elif args.delete:
        _delete_search_results(**kwargs)

    else:
        _display_search_results(**kwargs)


def _write_in_editor(config, template=None):
    if config["editor"]:
        logging.debug("Write mode: opening editor")
        if not template:
            template = _get_editor_template(config)
        raw = get_text_from_editor(config, template)

    else:
        raw = get_text_from_stdin()

    return raw


def _get_editor_template(config, **kwargs):
    logging.debug("Write mode: loading template for entry")

    if not config["template"]:
        logging.debug("Write mode: no template configured")
        return ""

    template_path = expand_path(config["template"])

    try:
        template = open(template_path).read()
        logging.debug("Write mode: template loaded: %s", template)
    except OSError:
        logging.error("Write mode: template not loaded")
        raise JrnlException(
            Message(
                MsgText.CantReadTemplate,
                MsgType.ERROR,
                {"template": template_path},
            )
        )

    return template


def _search_journal(args, journal, **kwargs):
    """Search the journal with the given args"""
    if args.on_date:
        args.start_date = args.end_date = args.on_date

    if args.today_in_history:
        now = time.parse("now")
        args.day = now.day
        args.month = now.month

    journal.filter(
        tags=args.text,
        month=args.month,
        day=args.day,
        year=args.year,
        start_date=args.start_date,
        end_date=args.end_date,
        strict=args.strict,
        starred=args.starred,
        exclude=args.excluded,
        contains=args.contains,
    )
    journal.limit(args.limit)


def _other_entries(journal, entries):
    """Find entries that are not in journal"""
    return [e for e in entries if e not in journal.entries]


def _edit_search_results(config, journal, old_entries, **kwargs):
    """
    1. Send the given journal entries to the user-configured editor
    2. Print out stats on any modifications to journal
    3. Write modifications to journal
    """
    if not config["editor"]:
        raise JrnlException(
            Message(
                MsgText.EditorNotConfigured,
                MsgType.ERROR,
                {"config_file": get_config_path()},
            )
        )

    # separate entries we are not editing
    other_entries = _other_entries(journal, old_entries)

    # Get stats now for summary later
    old_stats = _get_predit_stats(journal)

    # Send user to the editor
    edited = get_text_from_editor(config, journal.editable_str())
    journal.parse_editable_str(edited)

    # Print summary if available
    _print_edited_summary(journal, old_stats)

    # Put back entries we separated earlier, sort, and write the journal
    journal.entries += other_entries
    journal.sort()
    journal.write()


def _print_edited_summary(journal, old_stats, **kwargs):
    stats = {
        "added": len(journal) - old_stats["count"],
        "deleted": old_stats["count"] - len(journal),
        "modified": len([e for e in journal.entries if e.modified]),
    }

    prompts = []

    if stats["added"] > 0:
        prompts.append(f"{stats['added']} {_pluralize_entry(stats['added'])} added")
        stats["modified"] -= stats["added"]

    if stats["deleted"] > 0:
        prompts.append(
            f"{stats['deleted']} {_pluralize_entry(stats['deleted'])} deleted"
        )

    if stats["modified"]:
        prompts.append(
            f"{stats['modified']} {_pluralize_entry(stats['modified'])} modified"
        )

    if prompts:
        print(f"[{', '.join(prompts).capitalize()}]", file=sys.stderr)


def _get_predit_stats(journal):
    return {"count": len(journal)}


def _pluralize_entry(num):
    return "entry" if num == 1 else "entries"


def _delete_search_results(journal, old_entries, **kwargs):
    if not journal.entries:
        raise JrnlException(Message(MsgText.NothingToDelete, MsgType.ERROR))

    entries_to_delete = journal.prompt_action_entries("Delete entry")

    if entries_to_delete:
        journal.entries = old_entries
        journal.delete_entries(entries_to_delete)

        journal.write()


def _change_time_search_results(args, journal, old_entries, no_prompt=False, **kwargs):
    if not journal.entries:
        raise JrnlException(Message(MsgText.NothingToModify, MsgType.WARNING))

    # separate entries we are not editing
    other_entries = _other_entries(journal, old_entries)

    if no_prompt:
        entries_to_change = journal.entries
    else:
        entries_to_change = journal.prompt_action_entries("Change time")

    if entries_to_change:
        other_entries += [e for e in journal.entries if e not in entries_to_change]
        journal.entries = entries_to_change

        date = time.parse(args.change_time)
        journal.change_date_entries(date)

        journal.entries += other_entries
        journal.sort()
        journal.write()


def _display_search_results(args, journal, **kwargs):
    if args.short or args.export == "short":
        print(journal.pprint(short=True))

    elif args.export == "pretty":
        print(journal.pprint())

    elif args.tags:
        print(plugins.get_exporter("tags").export(journal))

    elif args.export:
        exporter = plugins.get_exporter(args.export)
        print(exporter.export(journal, args.filename))
    elif kwargs["config"].get("display_format"):
        exporter = plugins.get_exporter(kwargs["config"]["display_format"])
        print(exporter.export(journal, args.filename))
    else:
        print(journal.pprint())
