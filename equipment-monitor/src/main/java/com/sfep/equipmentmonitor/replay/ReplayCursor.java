package com.sfep.equipmentmonitor.replay;

import com.sfep.equipmentmonitor.bundle.BundleLoadException;
import com.sfep.equipmentmonitor.bundle.SafeFile;
import org.apache.commons.csv.CSVFormat;
import org.apache.commons.csv.CSVParser;
import org.apache.commons.csv.CSVRecord;

import java.io.FilterInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.Reader;
import java.io.UncheckedIOException;
import java.nio.ByteBuffer;
import java.nio.channels.Channels;
import java.nio.channels.FileChannel;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.LinkOption;
import java.nio.file.OpenOption;
import java.nio.file.StandardOpenOption;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.Iterator;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;

/** Streams replay rows from one verified open file descriptor with one-event lookahead. */
public final class ReplayCursor implements ReplayUnitCursor {
    private final ReplaySource source;
    private final SafeFile snapshot;
    private final FileChannel channel;
    private final CSVParser parser;
    private final Iterator<CSVRecord> records;
    private final CountingInputStream consumedBytes;
    private final MessageDigest consumedDigest;

    private ReplayEvent lookahead;
    private boolean eofVerified;
    private boolean closed;
    private long rowCount;
    private String firstEventId;
    private String lastEventId;

    private ReplayCursor(
            ReplaySource source,
            SafeFile snapshot,
            FileChannel channel,
            CSVParser parser,
            CountingInputStream consumedBytes,
            MessageDigest consumedDigest) {
        this.source = source;
        this.snapshot = snapshot;
        this.channel = channel;
        this.parser = parser;
        this.records = parser.iterator();
        this.consumedBytes = consumedBytes;
        this.consumedDigest = consumedDigest;
    }

    public static ReplayCursor open(ReplaySource source) {
        Objects.requireNonNull(source, "source");
        SafeFile snapshot = SafeFile.captureStandalone(source.path());
        FileChannel channel = null;
        CSVParser parser = null;
        try {
            channel = FileChannel.open(
                    snapshot.path(), Set.<OpenOption>of(StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS));
            snapshot.assertUnchanged("REPLAY_FILE_CHANGED");
            verifyDescriptor(channel, source);
            channel.position(0);

            MessageDigest consumedDigest = sha256();
            InputStream input = Channels.newInputStream(channel);
            DigestInputStream hashing = new DigestInputStream(input, consumedDigest);
            CountingInputStream counting = new CountingInputStream(hashing);
            var decoder = StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT);
            Reader reader = new InputStreamReader(counting, decoder);
            CSVFormat format = CSVFormat.RFC4180.builder()
                    .setHeader()
                    .setSkipHeaderRecord(true)
                    .get();
            parser = format.parse(reader);
            if (!parser.getHeaderNames().equals(ReplayCsvValidator.HEADER)) {
                throw new BundleLoadException(
                        "REPLAY_HEADER_INVALID",
                        "expected " + ReplayCsvValidator.HEADER + " but found " + parser.getHeaderNames());
            }
            return new ReplayCursor(source, snapshot, channel, parser, counting, consumedDigest);
        } catch (BundleLoadException error) {
            closeQuietly(parser, channel);
            throw error;
        } catch (UncheckedIOException | IOException error) {
            closeQuietly(parser, channel);
            if (hasCharacterCodingCause(error)) {
                throw new BundleLoadException("REPLAY_UTF8_INVALID", source.path().toString(), error);
            }
            throw new BundleLoadException("REPLAY_CSV_INVALID", source.path().toString(), error);
        } catch (RuntimeException error) {
            closeQuietly(parser, channel);
            throw error;
        }
    }

    @Override
    public Optional<ReplayUnit> nextUnit() {
        ensureOpen();
        ReplayEvent first = lookahead;
        lookahead = null;
        if (first == null) {
            first = nextEvent();
        }
        if (first == null) {
            return Optional.empty();
        }

        List<ReplayEvent> events = new ArrayList<>();
        events.add(first);
        while (true) {
            ReplayEvent event = nextEvent();
            if (event == null) {
                break;
            }
            if (event.batchId().equals(first.batchId())
                    && event.batchStep().equals(first.batchStep())) {
                events.add(event);
            } else {
                lookahead = event;
                break;
            }
        }
        return Optional.of(ReplayUnit.from(events));
    }

    private ReplayEvent nextEvent() {
        if (eofVerified) {
            return null;
        }
        try {
            if (!records.hasNext()) {
                verifyConsumedSnapshot();
                eofVerified = true;
                return null;
            }
            CSVRecord record = records.next();
            if (record.size() != ReplayCsvValidator.HEADER.size()) {
                throw new BundleLoadException(
                        "REPLAY_CSV_INVALID",
                        "record " + record.getRecordNumber() + " has " + record.size() + " columns");
            }
            ReplayEvent event = ReplayCsvValidator.bind(ReplayCsvValidator.rowNode(record));
            if (!source.bundleId().equals(event.bundleId())) {
                throw new BundleLoadException("REPLAY_BUNDLE_ID_MISMATCH", event.eventId());
            }
            if (!source.criteriaId().equals(event.criteriaId())) {
                throw new BundleLoadException("REPLAY_CRITERIA_ID_MISMATCH", event.eventId());
            }
            if (firstEventId == null) {
                firstEventId = event.eventId();
            }
            lastEventId = event.eventId();
            rowCount++;
            return event;
        } catch (BundleLoadException error) {
            throw error;
        } catch (UncheckedIOException error) {
            if (hasCharacterCodingCause(error)) {
                throw new BundleLoadException("REPLAY_UTF8_INVALID", source.path().toString(), error);
            }
            throw new BundleLoadException("REPLAY_CSV_INVALID", source.path().toString(), error);
        } catch (RuntimeException error) {
            if (hasCharacterCodingCause(error)) {
                throw new BundleLoadException("REPLAY_UTF8_INVALID", source.path().toString(), error);
            }
            throw new BundleLoadException("REPLAY_CSV_INVALID", source.path().toString(), error);
        }
    }

    private void verifyConsumedSnapshot() {
        if (consumedBytes.count() != source.sizeBytes()) {
            throw new BundleLoadException(
                    "REPLAY_CONSUMED_SIZE_MISMATCH",
                    "expected " + source.sizeBytes() + " but consumed " + consumedBytes.count());
        }
        String actualSha256 = digestUri(consumedDigest);
        if (!actualSha256.equals(source.sha256())) {
            throw new BundleLoadException(
                    "REPLAY_CONSUMED_HASH_MISMATCH",
                    "expected " + source.sha256() + " but consumed " + actualSha256);
        }
        ReplayMetadata expected = source.metadata();
        if (expected == null
                || rowCount != expected.rowCount()
                || !Objects.equals(firstEventId, expected.firstEventId())
                || !Objects.equals(lastEventId, expected.lastEventId())) {
            throw new BundleLoadException(
                    "REPLAY_METADATA_MISMATCH",
                    "consumed rows/events differ from validated replay metadata");
        }
        snapshot.assertUnchanged("REPLAY_FILE_CHANGED");
    }

    private static void verifyDescriptor(FileChannel channel, ReplaySource source) throws IOException {
        MessageDigest digest = sha256();
        long count = 0;
        ByteBuffer buffer = ByteBuffer.allocate(64 * 1024);
        while (true) {
            int read = channel.read(buffer);
            if (read < 0) {
                break;
            }
            if (read == 0) {
                continue;
            }
            count += read;
            buffer.flip();
            digest.update(buffer);
            buffer.clear();
        }
        if (count != source.sizeBytes()) {
            throw new BundleLoadException(
                    "REPLAY_DESCRIPTOR_SIZE_MISMATCH",
                    "expected " + source.sizeBytes() + " but descriptor has " + count);
        }
        String actual = digestUri(digest);
        if (!actual.equals(source.sha256())) {
            throw new BundleLoadException(
                    "REPLAY_DESCRIPTOR_HASH_MISMATCH",
                    "expected " + source.sha256() + " but descriptor has " + actual);
        }
    }

    private void ensureOpen() {
        if (closed) {
            throw new IllegalStateException("replay cursor is closed");
        }
    }

    @Override
    public boolean exhausted() {
        return eofVerified && lookahead == null;
    }

    @Override
    public void close() {
        if (closed) {
            return;
        }
        closed = true;
        try {
            parser.close();
        } catch (IOException error) {
            throw new BundleLoadException("REPLAY_CLOSE_FAILED", source.path().toString(), error);
        } finally {
            try {
                channel.close();
            } catch (IOException ignored) {
                // Parser normally owns the channel; a second close is harmless.
            }
        }
    }

    private static void closeQuietly(CSVParser parser, FileChannel channel) {
        try {
            if (parser != null) {
                parser.close();
            } else if (channel != null) {
                channel.close();
            }
        } catch (IOException ignored) {
            // Preserve the construction failure.
        }
    }

    private static MessageDigest sha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 unavailable", impossible);
        }
    }

    private static String digestUri(MessageDigest digest) {
        return "sha256:" + HexFormat.of().formatHex(digest.digest());
    }

    private static boolean hasCharacterCodingCause(Throwable error) {
        for (Throwable current = error; current != null; current = current.getCause()) {
            if (current instanceof CharacterCodingException) {
                return true;
            }
        }
        return false;
    }

    private static final class CountingInputStream extends FilterInputStream {
        private long count;

        private CountingInputStream(InputStream input) {
            super(input);
        }

        @Override
        public int read() throws IOException {
            int value = super.read();
            if (value >= 0) {
                count++;
            }
            return value;
        }

        @Override
        public int read(byte[] bytes, int offset, int length) throws IOException {
            int consumed = super.read(bytes, offset, length);
            if (consumed > 0) {
                count += consumed;
            }
            return consumed;
        }

        private long count() {
            return count;
        }
    }
}
