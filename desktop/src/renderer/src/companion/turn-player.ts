/** One generation owns its playback queue. Stale async completions have no effect. */
export class TurnPlayer<T> {
  private turn: number | null = null;
  private lastTurn = -1;
  private epoch = 0;
  private queue: T[] = [];
  private running = false;
  private ended = false;
  private play: (item: T, current: () => boolean) => Promise<void>;
  private cancel: () => void;
  private complete: (turn: number) => void;
  private error: (error: unknown) => void;

  constructor(play: (item: T, current: () => boolean) => Promise<void>, cancel: () => void,
    complete = (_turn: number) => {}, error = (_error: unknown) => {}) {
    this.play = play;
    this.cancel = cancel;
    this.complete = complete;
    this.error = error;
  }

  begin(turn: number) {
    if (turn <= this.lastTurn) return;
    this.stop();
    this.turn = turn;
    this.lastTurn = turn;
  }

  stop() {
    this.epoch += 1;
    this.turn = null;
    this.queue = [];
    this.running = false;
    this.ended = false;
    this.cancel();
  }

  enqueue(turn: number, item: T) {
    if (turn !== this.turn) return;
    this.queue.push(item);
    void this.drain();
  }

  end(turn: number) {
    if (turn !== this.turn) return;
    this.ended = true;
    void this.drain();
  }

  private async drain() {
    if (this.running || this.turn === null) return;
    const epoch = this.epoch;
    this.running = true;
    const current = () => epoch === this.epoch;
    try {
      while (this.queue.length && current()) {
        await this.play(this.queue.shift()!, current);
      }
    } catch (error) {
      if (current()) this.error(error);
    } finally {
      if (current()) {
        this.running = false;
        if (this.queue.length) void this.drain();
        else if (this.ended && this.turn !== null) {
          this.ended = false;
          this.complete(this.turn);
        }
      }
    }
  }
}
