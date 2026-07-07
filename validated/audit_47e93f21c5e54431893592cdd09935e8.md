### Title
Fee Truncation to Zero on Small Fast Withdrawals for Low-Decimal Tokens — (File: `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`fastWithdrawalFeeAmount` in `BaseWithdrawPool.sol` converts a fixed-point X18 fee back to native token decimals using raw Solidity integer division (`feeX18 / int128(multiplier)`). For tokens with fewer than 18 decimals (e.g., USDC with 6 decimals), the `multiplier` is `10^12`. Any withdrawal amount small enough to produce `feeX18 < multiplier` causes the fee to truncate to exactly `0`. A user can deliberately submit fast withdrawals below this threshold to pay zero protocol fees.

---

### Finding Description

`fastWithdrawalFeeAmount` computes the fee in X18 fixed-point format and then converts it back to native token units on line 148:

```solidity
return feeX18 / int128(multiplier);
``` [1](#0-0) 

The `multiplier` is `10**(MAX_DECIMALS - decimals)`. For a 6-decimal token (USDC), `multiplier = 10^12`.

The proportional fee path computes:

```
proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18)
                   = (1e15 * amount * 1e12) / 1e18
                   = amount * 1e9
``` [2](#0-1) [3](#0-2) 

For `proportionalFeeX18 < multiplier = 1e12`, the final division truncates to 0:

```
amount * 1e9 < 1e12  →  amount < 1000
```

So for any USDC fast withdrawal of fewer than 1000 units (< $0.001), `fastWithdrawalFeeAmount` returns `0`. The `minFeeX18` path (`5 * withdrawFeeX18`) is subject to the same truncation if `withdrawFeeX18` is small. [4](#0-3) 

Back in `submitFastWithdrawal`, when `fee = 0`:

- `fees[productId] += 0` — no fee is recorded
- `transferAmount -= 0` — user receives the full withdrawal amount [5](#0-4) 

---

### Impact Explanation

The protocol's fast-withdrawal fee accounting is corrupted: `fees[productId]` is never incremented for affected withdrawals, and the user receives the full `transferAmount` without any fee deduction. This is a direct, concrete asset delta: the protocol collects $0 in fees on withdrawals that should incur a 0.1% charge. An adversary can split a large withdrawal into many small sub-threshold transactions to avoid all fast-withdrawal fees entirely.

---

### Likelihood Explanation

`submitFastWithdrawal` is a public function callable by any address holding a valid verifier-signed transaction. [6](#0-5) 

A user controls the `amount` field in their withdrawal request. Requesting amounts below the truncation threshold (< 1000 USDC units for a 6-decimal token) is trivially achievable and produces a verifier-signed transaction that, when submitted, yields `fee = 0`. No privileged access, governance capture, or key compromise is required.

---

### Recommendation

Replace the raw integer division on line 148 with a ceiling division to ensure the fee is always rounded up (never down to zero):

```solidity
// Instead of: return feeX18 / int128(multiplier);
int128 m = int128(multiplier);
return (feeX18 + m - 1) / m;
```

Additionally, enforce a minimum fee floor at the native-token level (after conversion) so that any non-zero withdrawal always incurs at least 1 unit of fee, consistent with the intent of `minFeeX18`. [7](#0-6) 

---

### Proof of Concept

**Token:** USDC (6 decimals)
**Constants:** `MAX_DECIMALS = 18`, `FAST_WITHDRAWAL_FEE_RATE = 1e15`

```
multiplier       = 10^(18-6) = 10^12
amount           = 999  (i.e., $0.000999 USDC)
amountX18        = 999 * 10^12 = 999_000_000_000_000
proportionalFeeX18 = (1e15 * 999_000_000_000_000) / 1e18
                   = 999_000_000_000   (<  multiplier = 1e12)
feeX18           = max(999_000_000_000, minFeeX18)  ← assume minFeeX18 ≤ 999_000_000_000
fee              = 999_000_000_000 / 1_000_000_000_000 = 0  ← truncated
```

Result: `fees[productId] += 0`, user receives full `transferAmount = 999` USDC units with no fee deducted. [8](#0-7) [9](#0-8)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L81-85)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
```

**File:** core/contracts/BaseWithdrawPool.sol (L102-113)
```text
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

**File:** core/contracts/BaseWithdrawPool.sol (L134-149)
```text
    function fastWithdrawalFeeAmount(
        IERC20Base token,
        uint32 productId,
        uint128 amount
    ) public view returns (int128) {
        uint8 decimals = token.decimals();
        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));
        int128 amountX18 = int128(amount) * int128(multiplier);

        int128 proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18);
        int128 minFeeX18 = 5 * spotEngine().getConfig(productId).withdrawFeeX18;

        int128 feeX18 = MathHelper.max(proportionalFeeX18, minFeeX18);
        return feeX18 / int128(multiplier);
    }
```

**File:** core/contracts/common/Constants.sol (L19-25)
```text
uint8 constant MAX_DECIMALS = 18;

int128 constant TAKER_SEQUENCER_FEE = 0; // $0.00

int128 constant SLOW_MODE_FEE = 1000000; // $1

int128 constant FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000; // 0.1%
```
