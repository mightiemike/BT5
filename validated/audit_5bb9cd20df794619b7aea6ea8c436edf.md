### Title
`requireValidTxSignatures` Digest Omits Contract Address, Enabling Cross-Pool Signature Replay — (`File: core/contracts/Verifier.sol`)

---

### Summary

`Verifier.requireValidTxSignatures` constructs its signed digest using only `chainid`, `idx`, and `txn` — with no `WithdrawPool` contract address. Any valid set of sequencer signatures for a fast withdrawal on one `WithdrawPool` instance can be replayed verbatim on a second `WithdrawPool` instance that shares the same `Verifier`, draining tokens from the second pool without a corresponding off-chain withdrawal request.

---

### Finding Description

`Verifier.requireValidTxSignatures` builds the message digest as:

```solidity
bytes32 data = keccak256(
    abi.encodePacked(uint256(block.chainid), uint256(idx), txn)
);
``` [1](#0-0) 

No `WithdrawPool` address (or any contract-identifying context) is included. The replay guard in `BaseWithdrawPool.submitFastWithdrawal` is a per-instance `markedIdxs[idx]` mapping:

```solidity
require(!markedIdxs[idx], "Withdrawal already submitted");
markedIdxs[idx] = true;
``` [2](#0-1) 

This mapping lives in each pool's own storage. A second `WithdrawPool` instance that shares the same `Verifier` has its own independent `markedIdxs`, so the same `(idx, txn, signatures)` tuple passes both the replay check and the signature check on the second pool.

`submitFastWithdrawal` is `public` with no caller restriction:

```solidity
function submitFastWithdrawal(
    uint64 idx,
    bytes calldata transaction,
    bytes[] calldata signatures
) public {
``` [3](#0-2) 

The `Verifier` address is injected at initialization time and can be shared across multiple `WithdrawPool` deployments:

```solidity
function _initialize(address _clearinghouse, address _verifier) internal initializer {
    clearinghouse = _clearinghouse;
    verifier = _verifier;
}
``` [4](#0-3) 

---

### Impact Explanation

An attacker who observes a valid `submitFastWithdrawal(idx, txn, signatures)` call accepted by pool A can immediately replay the identical calldata against pool B. Pool B's `markedIdxs[idx]` is `false` (independent storage), `idx > minIdx` passes if pool B has not yet processed a sequencer withdrawal at that index, and `requireValidTxSignatures` accepts the signatures because the digest is identical. Pool B then executes `handleWithdrawTransfer`, transferring tokens to the withdrawal recipient a second time — tokens that were never authorized to leave pool B.

The corrupted state delta: pool B's token balance is reduced by `transferAmount` without any corresponding off-chain debit, and `fees[productId]` is credited with a fee that was never legitimately collected.

---

### Likelihood Explanation

The Nado protocol is designed to support multiple `WithdrawPool` instances (the contract is a standalone upgradeable proxy, initialized with configurable `clearinghouse` and `verifier` addresses). A shared `Verifier` across pools is the natural deployment pattern since the sequencer key set is protocol-wide. Any deployment with two or more pools sharing a `Verifier` is immediately exploitable by any unprivileged caller who can observe on-chain transactions — no special access is required.

---

### Recommendation

Include the calling `WithdrawPool` contract address in the signed digest inside `requireValidTxSignatures`, analogous to EIP-712's `verifyingContract` field:

```solidity
// In BaseWithdrawPool.submitFastWithdrawal, pass address(this):
v.requireValidTxSignatures(transaction, idx, signatures, address(this));

// In Verifier.requireValidTxSignatures:
function requireValidTxSignatures(
    bytes calldata txn,
    uint64 idx,
    bytes[] calldata signatures,
+   address withdrawPool          // <-- new parameter
) public view {
    bytes32 data = keccak256(
-       abi.encodePacked(uint256(block.chainid), uint256(idx), txn)
+       abi.encodePacked(uint256(block.chainid), withdrawPool, uint256(idx), txn)
    );
    ...
}
```

This ensures a signature issued for pool A is cryptographically bound to pool A's address and cannot be accepted by pool B.

---

### Proof of Concept

1. Protocol deploys `WithdrawPoolA` and `WithdrawPoolB`, both initialized with the same `Verifier` address. Both pools hold USDC (same `productId`).
2. A legitimate user submits `submitFastWithdrawal(idx=5, txn=T, sigs=S)` to `WithdrawPoolA`. The call succeeds; `WithdrawPoolA.markedIdxs[5]` is set to `true`.
3. Attacker calls `WithdrawPoolB.submitFastWithdrawal(idx=5, txn=T, sigs=S)` with the identical parameters.
4. `WithdrawPoolB.markedIdxs[5]` is `false` → passes. `idx=5 > WithdrawPoolB.minIdx=0` → passes.
5. `Verifier.requireValidTxSignatures` computes `keccak256(abi.encodePacked(chainid, 5, T))` — identical to what was signed — and accepts `S`.
6. `resolveFastWithdrawal(T)` returns the same `(productId, sendTo, amount)`.
7. `WithdrawPoolB` transfers `amount` of USDC to `sendTo`, draining pool B's funds without any authorized withdrawal. [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/Verifier.sol (L261-289)
```text
    function requireValidTxSignatures(
        bytes calldata txn,
        uint64 idx,
        bytes[] calldata signatures
    ) public view {
        require(signatures.length <= 256, "too many signatures");
        bytes32 data = keccak256(
            abi.encodePacked(uint256(block.chainid), uint256(idx), txn)
        );
        bytes32 hashedMsg = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", data)
        );

        uint256 nSignatures = 0;
        for (uint256 i = 0; i < signatures.length; i++) {
            if (signatures[i].length > 0) {
                nSignatures += 1;
                require(
                    checkIndividualSignature(
                        hashedMsg,
                        signatures[i],
                        uint8(i)
                    ),
                    "invalid signature"
                );
            }
        }
        require(nSignatures == nSigner, "not enough signatures");
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L23-30)
```text
    function _initialize(address _clearinghouse, address _verifier)
        internal
        initializer
    {
        __Ownable_init();
        clearinghouse = _clearinghouse;
        verifier = _verifier;
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L81-113)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;

        Verifier v = Verifier(verifier);
        v.requireValidTxSignatures(transaction, idx, signatures);

        (
            uint32 productId,
            address sendTo,
            uint128 transferAmount
        ) = resolveFastWithdrawal(transaction);
        IERC20Base token = getToken(productId);

        require(transferAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);

        int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
```
