### Title
`ModifyCollateral` Event Omits Withdrawal Nonce and Submission Index, Enabling Off-Chain/On-Chain State Desynchronization — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary
The `ModifyCollateral` event emitted on every collateral withdrawal in `Clearinghouse.sol` does not include the withdrawal `nonce` (from the `WithdrawCollateral` struct) or the `idx` (submission index, i.e., `nSubmissions`) that uniquely identifies the processed transaction. Off-chain indexers watching this event cannot distinguish between two identical pending withdrawals from the same subaccount for the same product and amount, creating the same class of off-chain/on-chain desynchronization described in the reference report.

---

### Finding Description

The `ModifyCollateral` event is defined as:

```solidity
event ModifyCollateral(
    int128 amount,
    bytes32 indexed subaccount,
    uint32 productId
);
``` [1](#0-0) 

It is emitted at the end of `withdrawCollateral` without the `idx` parameter (which is `nSubmissions` at call time and serves as the unique per-submission identifier) and without the withdrawal `nonce`:

```solidity
function withdrawCollateral(
    bytes32 sender,
    uint32 productId,
    uint128 amount,
    address sendTo,
    uint64 idx          // unique submission index — NOT emitted
) public virtual onlyEndpoint {
    ...
    handleWithdrawTransfer(token, sendTo, amount, idx);   // idx used here
    ...
    emit ModifyCollateral(amountRealized, sender, productId);  // idx absent
}
``` [2](#0-1) 

The `WithdrawCollateral` struct carries a `nonce` field that is validated on-chain via `validateNonce` before the withdrawal is executed:

```solidity
struct WithdrawCollateral {
    bytes32 sender;
    uint32 productId;
    uint128 amount;
    uint64 nonce;       // validated on-chain, never emitted
}
``` [3](#0-2) 

The nonce is consumed in `validateSignedTx` → `validateNonce` before `clearinghouse.withdrawCollateral` is called: [4](#0-3) 

Neither the `nonce` nor the `idx` is forwarded into the emitted event. Two withdrawals from the same subaccount for the same `productId` and `amount` produce byte-for-byte identical `ModifyCollateral` log entries.

---

### Impact Explanation

Off-chain indexers (sequencer triage code, portfolio trackers, bridge relayers) that consume `ModifyCollateral` to determine *which* pending withdrawal was settled cannot distinguish between two identical pending withdrawals. If the indexer misidentifies which withdrawal was settled (e.g., marks withdrawal B as done when withdrawal A was actually processed), it may:

1. Leave withdrawal A in a "pending" state and re-queue or re-credit it.
2. Simultaneously allow the user to claim the funds from withdrawal A on-chain (already executed) and receive a second credit off-chain for the same amount.

The on-chain balance deduction is correct, but the off-chain accounting layer — which gates further actions such as re-crediting, re-submission, or cross-chain bridging — operates on ambiguous event data. The corrupted state is the off-chain pending-withdrawal queue and the associated user credit record.

---

### Likelihood Explanation

Any user can reach this path by calling `depositCollateralWithReferral` (which queues a slow-mode `DepositCollateral`) or by submitting a signed `WithdrawCollateral` through the sequencer. Two withdrawals of the same amount for the same product from the same subaccount are a realistic scenario (e.g., partial withdrawals of a recurring amount). The likelihood is **low-to-medium**: it requires the off-chain code to use `ModifyCollateral` as the sole correlation signal for pending withdrawals, which is the natural design given no other per-withdrawal event exists.

---

### Recommendation

**Short term:** Add `nonce` (for sequencer-path withdrawals) and `idx` (submission index) to the `ModifyCollateral` event so every emission is uniquely attributable to a specific on-chain transaction:

```solidity
event ModifyCollateral(
    int128 amount,
    bytes32 indexed subaccount,
    uint32 productId,
    uint64 nonce,   // withdrawal nonce; 0 for deposits
    uint64 idx      // nSubmissions at time of processing
);
```

**Long term:** Audit every event emitted across `Clearinghouse.sol`, `SpotEngineState.sol` (`SpotBalance`), and `BaseEngine.sol` (`BalanceUpdate`) to ensure each carries sufficient fields for off-chain code to unambiguously correlate the event to the originating signed transaction.

---

### Proof of Concept

1. Alice signs two `WithdrawCollateral` transactions: W1 (`nonce=5`, `amount=1000`, `productId=1`) and W2 (`nonce=6`, `amount=1000`, `productId=1`).
2. The sequencer processes W1 (`idx=100`): `emit ModifyCollateral(-1000e18, alice, 1)`.
3. The sequencer processes W2 (`idx=101`): `emit ModifyCollateral(-1000e18, alice, 1)`.
4. Both log entries are identical. An off-chain indexer that correlates events to pending withdrawals by `(subaccount, productId, amount)` cannot determine which nonce was settled first.
5. If the indexer processes the first event and marks W2 (nonce=6) as settled (instead of W1), it leaves W1 in a "pending" state. Depending on the off-chain retry/re-credit logic, Alice may receive a second credit for W1 while the on-chain balance has already been deducted for both.

The root cause is solely in `Clearinghouse.sol`'s `withdrawCollateral` at the `emit ModifyCollateral` call site, where `idx` and `nonce` are in scope but not forwarded to the event. [5](#0-4)

### Citations

**File:** core/contracts/interfaces/clearinghouse/IClearinghouseEventEmitter.sol (L9-13)
```text
    event ModifyCollateral(
        int128 amount,
        bytes32 indexed subaccount,
        uint32 productId
    );
```

**File:** core/contracts/Clearinghouse.sol (L391-421)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }
```

**File:** core/contracts/interfaces/IEndpoint.sol (L80-85)
```text
    struct WithdrawCollateral {
        bytes32 sender;
        uint32 productId;
        uint128 amount;
        uint64 nonce;
    }
```

**File:** core/contracts/EndpointTx.sol (L72-77)
```text
    function validateNonce(bytes32 sender, uint64 nonce) internal virtual {
        require(
            nonce == nonces[address(uint160(bytes20(sender)))]++,
            ERR_WRONG_NONCE
        );
    }
```
