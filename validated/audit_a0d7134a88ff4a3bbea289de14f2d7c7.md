### Title
Accumulated Fast Withdrawal Fees in `BaseWithdrawPool` Have No Dedicated Withdrawal Function, Causing Permanent Fee Lock and Accounting Desynchronization — (File: `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`BaseWithdrawPool.submitFastWithdrawal` accumulates protocol fee revenue into the `fees[productId]` mapping on every call, but no function exists to withdraw those fees while correctly decrementing the mapping. The only token-exit path, `removeLiquidity`, operates on the raw ERC20 balance and never touches `fees`, permanently desynchronizing on-chain accounting and leaving collected fee revenue with no proper extraction mechanism.

---

### Finding Description

Every call to `submitFastWithdrawal` collects a fee from the user and credits it to the contract's ERC20 balance, recording it in `fees[productId]`:

```solidity
// BaseWithdrawPool.sol line 111
fees[productId] += fee;
``` [1](#0-0) 

The two fee-collection paths are:
- **Self-submission** (`sendTo == msg.sender`): fee is deducted from `transferAmount`, so the contract retains the difference.
- **Third-party submission**: `safeTransferFrom(token, msg.sender, uint128(fee))` pulls the fee directly from the submitter into the contract. [2](#0-1) 

The only owner-accessible token-exit function is `removeLiquidity`:

```solidity
function removeLiquidity(uint32 productId, uint128 amount, address sendTo)
    external onlyOwner {
    handleWithdrawTransfer(getToken(productId), sendTo, amount);
}
``` [3](#0-2) 

`removeLiquidity` transfers raw ERC20 tokens out of the contract but **never decrements `fees[productId]`**. There is no `withdrawFees`, `claimFees`, or equivalent function anywhere in the contract or its child `WithdrawPool`. [4](#0-3) 

This creates two compounding problems:

1. **No clean fee extraction path**: The contract's token balance conflates user liquidity (deposited to service withdrawals) with accumulated protocol fees. Calling `removeLiquidity` to extract fees also drains the liquidity pool, potentially leaving the contract unable to service pending `submitWithdrawal` calls from the clearinghouse.

2. **Permanent accounting desynchronization**: `fees[productId]` is a public state variable that only ever increases. After any use of `removeLiquidity` to recover fee tokens, the mapping permanently overstates the true uncollected fee balance, breaking any off-chain or on-chain system that reads it.

---

### Impact Explanation

Protocol fee revenue (real ERC20 tokens) accumulates in `BaseWithdrawPool` with no safe, accounting-correct extraction path. The only available workaround (`removeLiquidity`) conflates fee tokens with user liquidity, meaning extracting fees risks undercollateralizing the pool for pending user withdrawals. The `fees` mapping becomes permanently stale, corrupting protocol revenue accounting. This is a direct analog to the reported MarginFi pattern: a deposit/collection path exists (`submitFastWithdrawal` → `fees[productId] += fee`) but the corresponding withdrawal/management path is absent.

---

### Likelihood Explanation

`submitFastWithdrawal` is a `public` function callable by any unprivileged user. [5](#0-4)  Fee accumulation occurs on every fast withdrawal processed by the protocol. Given that fast withdrawals are a core user-facing feature, fee accumulation is continuous and the missing withdrawal function is immediately impactful from the first fast withdrawal processed.

---

### Recommendation

Add a dedicated `withdrawFees` function that:
1. Reads `fees[productId]` to determine the withdrawable amount.
2. Resets `fees[productId]` to zero before transferring (checks-effects-interactions).
3. Calls `handleWithdrawTransfer` to send only the fee portion to a designated recipient.

This cleanly separates fee revenue from user liquidity and keeps the `fees` mapping accurate.

---

### Proof of Concept

1. Owner deploys `WithdrawPool`, seeds it with 10,000 USDC of liquidity to service withdrawals.
2. User A calls `submitFastWithdrawal` with a valid signed withdrawal for 1,000 USDC. Fee = 5 USDC. Contract balance: 10,005 USDC. `fees[productId]` = 5.
3. User B calls `submitFastWithdrawal` for 2,000 USDC. Fee = 10 USDC. Contract balance: 10,015 USDC. `fees[productId]` = 15.
4. Owner wants to collect the 15 USDC in fees. The only available function is `removeLiquidity(productId, 15, owner)`.
5. `removeLiquidity` transfers 15 USDC to the owner but `fees[productId]` remains 15 — permanently stale.
6. If the owner calls `removeLiquidity` again to collect future fees, they must guess the correct amount from off-chain data; the on-chain `fees` mapping is useless as a reference.
7. If the owner over-extracts (e.g., calls `removeLiquidity` for the full balance), the pool is drained and subsequent `submitWithdrawal` calls from the clearinghouse will revert on the ERC20 transfer, blocking all standard user withdrawals. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L39-41)
```text
    // collected withdrawal fees in native token decimals
    mapping(uint32 => int128) public fees;

```

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

**File:** core/contracts/BaseWithdrawPool.sol (L151-157)
```text
    function removeLiquidity(
        uint32 productId,
        uint128 amount,
        address sendTo
    ) external onlyOwner {
        handleWithdrawTransfer(getToken(productId), sendTo, amount);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L184-190)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount
    ) internal virtual {
        token.safeTransfer(to, uint256(amount));
    }
```

**File:** core/contracts/WithdrawPool.sol (L1-19)
```text
// SPDX-License-Identifier: GPL-2.0-or-later
pragma solidity ^0.8.0;

import "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/utils/cryptography/draft-EIP712Upgradeable.sol";
import "./libraries/MathHelper.sol";
import "./interfaces/IEndpoint.sol";
import "./Verifier.sol";
import "./interfaces/engine/ISpotEngine.sol";
import "./interfaces/IERC20Base.sol";
import "./libraries/ERC20Helper.sol";
import "./common/Constants.sol";
import "./BaseWithdrawPool.sol";

contract WithdrawPool is BaseWithdrawPool {
    function initialize(address _clearinghouse, address _verifier) external {
        _initialize(_clearinghouse, _verifier);
    }
}
```
