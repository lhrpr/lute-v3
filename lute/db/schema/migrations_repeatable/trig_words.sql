DROP TRIGGER IF EXISTS trig_words_after_update_WoStatus_if_following_parent;

CREATE TRIGGER trig_words_after_update_WoStatus_if_following_parent
-- created by db/schema/migrations_repeatable/trig_words.sql
AFTER UPDATE OF WoStatus, WoSyncStatus ON words
FOR EACH ROW
WHEN (old.WoStatus <> new.WoStatus or (old.WoSyncStatus = 0 and new.WoSyncStatus = 1))
  -- Device sync: while remote changes are being applied, incoming values are
  -- authoritative and no derived mutation may run.  See trig_synclog.sql.
  AND (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    UPDATE words
    SET WoStatus = new.WoStatus
    WHERE WoID in (
      -- single parent children that are following this term.
      select WpWoID
      from wordparents
      inner join words on WoID = WpWoID
      where WoSyncStatus = 1
      and WpParentWoID = old.WoID
      group by WpWoID
      having count(*) = 1

      UNION

      -- The parent of this term,
      -- if this term has a single parent and has "follow parent"
      select WpParentWoID
      from wordparents
      inner join words on WoID = WpWoID
      where WoSyncStatus = 1
      and WoID = old.WoID
      group by WpWoID
      having count(*) = 1
    );
END;


DROP TRIGGER IF EXISTS trig_words_update_WoStatusChanged;

CREATE TRIGGER trig_words_update_WoStatusChanged
-- created by db/schema/migrations_repeatable/trig_words.sql
AFTER UPDATE OF WoStatus ON words
FOR EACH ROW
WHEN old.WoStatus <> new.WoStatus
  -- Device sync: WoStatusChanged is the timestamp that decides which of two
  -- devices' status edits wins.  Stamping it with local time while applying a
  -- remote change would discard the origin time and break convergence, so the
  -- applied value is left exactly as the peer sent it.
  AND (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    UPDATE words
    SET WoStatusChanged = CURRENT_TIMESTAMP
    WHERE WoID = NEW.WoID;
END;


DROP TRIGGER IF EXISTS trig_words_update_WoCreated_if_no_longer_unknown;

CREATE TRIGGER trig_words_update_WoCreated_if_no_longer_unknown
-- created by db/schema/migrations_repeatable/trig_words.sql
AFTER UPDATE OF WoStatus ON words
FOR EACH ROW
WHEN old.WoStatus <> new.WoStatus and old.WoStatus = 0
  -- Device sync: WoCreated comes from the originating device.  See above.
  AND (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    UPDATE words
    SET WoCreated = CURRENT_TIMESTAMP
    WHERE WoID = NEW.WoID;
END;


DROP TRIGGER IF EXISTS trig_word_after_delete_change_WoSyncStatus_for_orphans;

CREATE TRIGGER trig_word_after_delete_change_WoSyncStatus_for_orphans
-- created by db/schema/migrations_repeatable/trig_words.sql
--
-- If a term is deleted, any orphaned children must
-- be updated to have WoSyncStatus = 0.
AFTER DELETE ON words
FOR EACH ROW
-- Device sync: the peer sends WoSyncStatus explicitly for every affected
-- term, so this repair must not run against applied changes.
WHEN (SELECT SsValue FROM syncstate WHERE SsKey = 'applying') = '0'
BEGIN
    UPDATE words
    SET WoSyncStatus = 0
    WHERE WoID NOT IN (SELECT WpWoID FROM wordparents);
END;
