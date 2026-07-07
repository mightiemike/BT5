### Title
Unprivileged Caller Can Force Any User Into an Arbitrary Fee Tier — (`core/contracts/EndpointTx.sol`)

---

### Summary

The `UpdateFeeTier` slow-mode transaction type carries no signature requirement from the user whose tier is being changed. Any unprivileged caller can pay the slow-mode fee, submit an `UpdateFeeTier` transaction naming an arbitrary victim address and any `newTier` value, and have the sequencer execute it — permanently overwriting the victim's fee tier in `OffchainExchange` without the victim's knowledge or consent.

---

### Finding Description

`IEndpoint.UpdateFeeTier` is a plain struct with two attacker-controlled fields:

```solidity
// core/contracts/interfaces/IEndpoint.sol  lines 233-236
struct UpdateFeeTier {
    address user;     // ← attacker picks any victim
    uint32 newTier;   // ← attacker picks any tier
}
``` [1](#0-0) 

When the sequencer calls `processTransactionImpl`, the `UpdateFeeTier` branch contains **no** `validateSignedTx` call — it passes the raw bytes directly to `Clearinghouse`:

```solidity
// core/contracts/EndpointTx.sol  lines 591-592
} else if (txType == IEndpoint.TransactionType.UpdateFeeTier) {
    clearinghouse.updateFeeTier(transaction);
``` [2](#0-1) 

Compare this with every other user-affecting transaction in the same function (e.g., `WithdrawCollateral`, `LinkSigner`, `TransferQuote`, `MintNlp`) — all of them call `validateSignedTx` before acting. `UpdateFeeTier` is the sole exception.

`Clearinghouse.updateFeeTier` simply decodes and forwards without any ownership check:

```solidity
// core/contracts/Clearinghouse.sol  lines 345-356
function updateFeeTier(bytes calldata transaction) external onlyEndpoint {
    IEndpoint.UpdateFeeTier memory txn = abi.decode(
        transaction[1:], (IEndpoint.UpdateFeeTier)
    );
    address offchainExchange = IEndpoint(getEndpoint()).getOffchainExchange();
    IOffchainExchange(offchainExchange).updateFeeTier(txn.user, txn.newTier);
}
``` [3](#0-2) 

`OffchainExchange.updateFeeTier` then unconditionally writes the new tier:

```solidity
// core/contracts/OffchainExchange.sol  lines 952-959
function updateFeeTier(address user, uint32 newTier) external {
    require(msg.sender == address(clearinghouse), ERR_UNAUTHORIZED);
    ...
    feeTiers[user] = newTier;
    emit FeeTierUpdate(user, newTier);
}
``` [4](#0-3) 

The slow-mode submission path does **not** restrict `UpdateFeeTier` to the contract owner. It falls into the generic `else` branch that charges only a small slow-mode fee and accepts the transaction from any `msg.sender`:

```solidity
// core/contracts/EndpointTx.sol  lines 355-372
} else if (
    txType == IEndpoint.TransactionType.WithdrawInsurance ||
    ...
    txType == IEndpoint.TransactionType.NlpProfitShare ||
    txType == IEndpoint.TransactionType.UpdateBuilder
) {
    require(sender == owner());   // ← UpdateFeeTier is NOT in this list
} else {
    chargeSlowModeFee(_getQuote(), sender);   // ← any user reaches here
    slowModeFees += SLOW_MODE_FEE;
}
``` [5](#0-4) 

---

### Impact Explanation

`feeTiers[user]` is the sole input to `getTierFeeRateX18`, which determines the maker/taker fee rates applied on every matched order for that address. [6](#0-5) 

An attacker can:

- **Downgrade** a victim who holds a favorable low-fee or rebate tier back to tier `0` (default 2 bps taker, 0 maker), permanently increasing their trading costs on every future trade.
- **Upgrade** a victim to a tier with a high taker fee, if such a tier exists, to sabotage their trading economics.

The state delta is `feeTiers[victim]` being overwritten to an attacker-chosen value. Because fee tiers directly affect the quote balance deducted on every `matchOrders` execution, this constitutes a concrete, ongoing financial impact to the victim.

---

### Likelihood Explanation

The attack requires only paying the slow-mode fee (a small fixed USDC amount). The entry point `submitSlowModeTransaction` is a public function callable by any EOA or contract. No privileged access, leaked keys, or social engineering is needed. The attacker can target any address that has been assigned a non-default fee tier, which is observable on-chain via `FeeTierUpdate` events.

---

### Recommendation

In `processTransactionImpl`, add a signature validation step for `UpdateFeeTier` that requires a valid signature from `txn.user` (or their linked signer) before applying the tier change — mirroring the pattern used by `LinkSigner`, `TransferQuote`, and all other user-state-mutating transaction types. Alternatively, restrict `UpdateFeeTier` to the contract owner in `submitSlowModeTransactionImpl` by adding it to the owner-only list alongside `UpdateTierFeeRates`.

---

### Proof of Concept

1. Victim `alice` has been assigned fee tier `5` (favorable low-fee tier) via a legitimate sequencer `UpdateFeeTier` transaction.
2. Attacker calls `Endpoint.submitSlowModeTransaction(abi.encodePacked(uint8(TransactionType.UpdateFeeTier), abi.encode(UpdateFeeTier({ user: alice, newTier: 0 }))))`, paying only the slow-mode fee.
3. After `SLOW_MODE_TX_DELAY` (3 days), the sequencer executes the slow-mode queue entry via `processSlowModeTransactionImpl`.
4. `processTransactionImpl` dispatches to the `UpdateFeeTier` branch with no signature check, calling `clearinghouse.updateFeeTier(transaction)`.
5. `Clearinghouse.updateFeeTier` decodes `user = alice, newTier = 0` and calls `OffchainExchange.updateFeeTier(alice, 0)`.
6. `feeTiers[alice]` is now `0`. All of Alice's subsequent trades are charged the default 2 bps taker fee instead of her previously earned favorable rate, with no action taken by Alice and no consent required. [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L233-236)
```text
    struct UpdateFeeTier {
        address user;
        uint32 newTier;
    }
```

**File:** core/contracts/EndpointTx.sol (L355-372)
```text
        } else if (
            txType == IEndpoint.TransactionType.WithdrawInsurance ||
            txType == IEndpoint.TransactionType.DelistProduct ||
            txType == IEndpoint.TransactionType.DumpFees ||
            txType == IEndpoint.TransactionType.RebalanceXWithdraw ||
            txType == IEndpoint.TransactionType.UpdateTierFeeRates ||
            txType == IEndpoint.TransactionType.AddNlpPool ||
            txType == IEndpoint.TransactionType.UpdateNlpPool ||
            txType == IEndpoint.TransactionType.DeleteNlpPool ||
            txType == IEndpoint.TransactionType.ForceRebalanceNlpPool ||
            txType == IEndpoint.TransactionType.NlpProfitShare ||
            txType == IEndpoint.TransactionType.UpdateBuilder
        ) {
            require(sender == owner());
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/EndpointTx.sol (L591-592)
```text
        } else if (txType == IEndpoint.TransactionType.UpdateFeeTier) {
            clearinghouse.updateFeeTier(transaction);
```

**File:** core/contracts/Clearinghouse.sol (L345-356)
```text
    function updateFeeTier(bytes calldata transaction) external onlyEndpoint {
        IEndpoint.UpdateFeeTier memory txn = abi.decode(
            transaction[1:],
            (IEndpoint.UpdateFeeTier)
        );
        address offchainExchange = IEndpoint(getEndpoint())
            .getOffchainExchange();
        IOffchainExchange(offchainExchange).updateFeeTier(
            txn.user,
            txn.newTier
        );
    }
```

**File:** core/contracts/OffchainExchange.sol (L933-945)
```text
    function getTierFeeRateX18(uint32 tier, uint32 productId)
        public
        view
        returns (FeeRates memory)
    {
        if (nonDefaultFeeTierMask & (1 << tier) != 0) {
            return feeRates[tier][productId];
        }
        return
            FeeRates({
                makerRateX18: 0,
                takerRateX18: 200_000_000_000_000 // 2 bps
            });
```

**File:** core/contracts/OffchainExchange.sol (L952-959)
```text
    function updateFeeTier(address user, uint32 newTier) external {
        require(msg.sender == address(clearinghouse), ERR_UNAUTHORIZED);
        if (newTier != 0 && !addressTouched[user]) {
            addressTouched[user] = true;
            customFeeAddresses.push(user);
        }
        feeTiers[user] = newTier;
        emit FeeTierUpdate(user, newTier);
```
