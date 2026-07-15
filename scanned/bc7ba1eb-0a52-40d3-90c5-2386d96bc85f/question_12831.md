# Q12831: submit promise resume data async receipt lifecycle

## Question

What can an unprivileged user do by deploying WASM bytecode and invoking exported contract methods with chosen arguments so that `submit_promise_resume_data` in `runtime/near-vm-runner/src/logic/mocks/mock_external.rs` processes yield/resume payloads, promise dependencies, delayed receipts, instant receipts, and timeout heights along the WASM preparation and execution path? User controls yield/resume payloads, promise dependencies, delayed receipts, instant receipts, and timeout heights -> `submit_promise_resume_data` processes that value during postponed receipt storage, data receipt delivery, yield timeout, resume handling, and receipt cleanup -> the asynchronous receipts have a single lifecycle state and cannot be resumed, timed out, refunded, or executed twice invariant might break -> potential in-scope impact is contract execution flow corruption, replay, or balance manipulation under the NEAR HackenProof scope. Exploit hypothesis: a user-controlled timing/order edge can make this code transition an async receipt through two terminal states, violating the actual protocol invariant that asynchronous receipts have a single lifecycle state and cannot be resumed, timed out, refunded, or executed twice.

## Target

- File/function: runtime/near-vm-runner/src/logic/mocks/mock_external.rs:275::submit_promise_resume_data
- Entrypoint: contract deployment and function call executed through runtime/near-vm-runner/src/runner.rs::run
- User-controlled input: yield/resume payloads, promise dependencies, delayed receipts, instant receipts, and timeout heights
- Attack path: User controls yield/resume payloads, promise dependencies, delayed receipts, instant receipts, and timeout heights -> public entrypoint reaches `submit_promise_resume_data` -> postponed receipt storage, data receipt delivery, yield timeout, resume handling, and receipt cleanup handles the value -> invariant failure could produce contract execution flow corruption, replay, or balance manipulation
- Security invariant: asynchronous receipts have a single lifecycle state and cannot be resumed, timed out, refunded, or executed twice
- Expected bounty impact: contract execution flow corruption, replay, or balance manipulation
- Fast validation approach: create contracts that interleave yield, resume, timeout, callbacks, and delayed queues, then assert every receipt ID has one terminal outcome
