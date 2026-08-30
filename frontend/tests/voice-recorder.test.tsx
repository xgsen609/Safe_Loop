import { NextIntlClientProvider, type AbstractIntlMessages } from "next-intl";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";

import { VoiceRecorder, formatRecordingTime } from "../components/reports/VoiceRecorder";
import en from "../messages/en.json";

function expand(flat: Record<string, string>): AbstractIntlMessages {
  const result: AbstractIntlMessages = {};
  for (const [key, value] of Object.entries(flat)) {
    const parts = key.split(".");
    let cursor: AbstractIntlMessages = result;
    for (const part of parts.slice(0, -1)) cursor = (cursor[part] ??= {}) as AbstractIntlMessages;
    cursor[parts.at(-1)!] = value;
  }
  return result;
}

function Harness({ variant = "inline" }: { variant?: "inline" | "hero" }) {
  const [file, setFile] = useState<File | null>(null);
  return (
    <NextIntlClientProvider locale="en" messages={expand(en)}>
      <label htmlFor="typed-description">Typed description</label>
      <textarea id="typed-description" defaultValue="Typing still works" />
      <VoiceRecorder value={file} onChange={setFile} variant={variant} />
    </NextIntlClientProvider>
  );
}

function DelayedLiveHarness() {
  const [file, setFile] = useState<File | null>(null);
  return (
    <NextIntlClientProvider locale="en" messages={expand(en)}>
      <VoiceRecorder
        value={file}
        onChange={setFile}
        startLive={() => new Promise(() => undefined)}
        variant="hero"
      />
    </NextIntlClientProvider>
  );
}

class MockMediaRecorder {
  static isTypeSupported = () => true;
  readonly mimeType: string;
  state: RecordingState = "inactive";
  ondataavailable: ((event: BlobEvent) => void) | null = null;
  onstop: (() => void) | null = null;

  constructor(_stream: MediaStream, options?: MediaRecorderOptions) {
    this.mimeType = options?.mimeType ?? "audio/webm";
  }

  start() {
    this.state = "recording";
  }

  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["voice"], { type: "audio/webm" }) } as BlobEvent);
    this.onstop?.();
  }
}

describe("VoiceRecorder", () => {
  const stopTrack = vi.fn();

  beforeEach(() => {
    stopTrack.mockReset();
    Object.defineProperty(globalThis, "MediaRecorder", {
      configurable: true,
      value: MockMediaRecorder,
    });
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn(async () => ({
          getTracks: () => [{ stop: stopTrack }],
        } as unknown as MediaStream)),
      },
    });
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:test-audio"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("formats the visible timer and caps it at two minutes", () => {
    expect(formatRecordingTime(0)).toBe("0:00");
    expect(formatRecordingTime(30)).toBe("0:30");
    expect(formatRecordingTime(120)).toBe("2:00");
    expect(formatRecordingTime(121)).toBe("2:00");
  });

  it("records, stops, and offers local playback and re-recording", async () => {
    render(<Harness />);
    const recordButton = await screen.findByRole("button", { name: en["report.voice.record"] });
    fireEvent.click(recordButton);

    expect(await screen.findByText(en["report.voice.recording"])).toBeTruthy();
    expect(screen.getByText("0:00 / 2:00")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: en["report.voice.stop"] }));

    expect(await screen.findByRole("button", { name: en["report.voice.recordAgain"] })).toBeTruthy();
    expect(document.querySelector("audio")?.getAttribute("src")).toBe("blob:test-audio");
    expect(stopTrack).toHaveBeenCalled();
  });

  it("presents the recorder as the primary action in voice-first mode", async () => {
    render(<Harness variant="hero" />);

    expect(await screen.findByText(en["report.voice.heroPrompt"])).toBeTruthy();
    expect(screen.getByText(en["report.voice.heroTime"])).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: en["report.voice.record"] }));

    expect(await screen.findByRole("button", { name: en["report.voice.stop"] })).toBeTruthy();
    expect(screen.getByText(en["report.voice.recording"])).toBeTruthy();
  });

  it("starts recording without waiting for the live transcript connection", async () => {
    render(<DelayedLiveHarness />);

    fireEvent.click(await screen.findByRole("button", { name: en["report.voice.record"] }));

    expect(await screen.findByRole("button", { name: en["report.voice.stop"] })).toBeTruthy();
  });

  it("hides itself after microphone denial while typed input remains", async () => {
    vi.mocked(navigator.mediaDevices.getUserMedia).mockRejectedValueOnce(
      new DOMException("denied", "NotAllowedError"),
    );
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: en["report.voice.record"] }));

    await waitFor(() => {
      expect(screen.queryByText(en["report.voice.title"])).toBeNull();
    });
    expect((screen.getByLabelText("Typed description") as HTMLTextAreaElement).value)
      .toBe("Typing still works");
  });
});
