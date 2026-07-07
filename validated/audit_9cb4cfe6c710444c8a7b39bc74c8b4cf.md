### Title
Fee-on-Transfer Token Accounting Corruption in Deposit Flow — (`core/contracts/EndpointStorage.sol`)

---

### Summary

`handleDepositTransfer` in `EndpointStorage.sol` performs two sequential ERC20 transfers using the caller-supplied `amount` without measuring actual received balances. For fee-on-transfer tokens, the endpoint receives less than `amount` on the first leg, then attempts to forward the full `amount` to the clearinghouse on the second leg — either reverting (DoS) or draining other depositors' funds. Separately, the slow-mode queue records the original `amount`, so `Clearinghouse.depositCollateral` credits the user's spot balance for more tokens than were ever received.

---

### Finding Description

The deposit entry path is:

**`Endpoint.depositCollateralWithReferral`** → **`EndpointStorage.handleDepositTransfer`** → slow-mode queue → **`Clearinghouse.depositCollateral`**

`handleDepositTransfer` executes two transfers with the same nominal `amount`:

```solidity
// EndpointStorage.sol:111-119
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    safeTransferFrom(token, from, amount);        // leg 1: user → endpoint
    safeTransferTo(token, address(clearinghouse), amount); // leg 2: endpoint → clearinghouse
}
``` [1](#0-0) 

For a fee-on-transfer token with fee rate `f`:
- Leg 1 delivers `amount * (1 - f)` to the endpoint.
- Leg 2 attempts to send `amount` from the endpoint to the clearinghouse, but the endpoint only holds `amount * (1 - f)`. This either reverts (blocking all deposits) or silently succeeds by consuming tokens deposited by other users.

After both legs, the slow-mode transaction is enqueued with the original `amount`:

```solidity
// Endpoint.sol:152-164
slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
    ...
    tx: abi.encodePacked(
        uint8(TransactionType.DepositCollateral),
        abi.encode(
            DepositCollateral({
                sender: subaccount,
                productId: productId,
                amount: amount          // ← original caller-supplied value
            })
        )
    )
});
``` [2](#0-1) 

When the sequencer processes this slow-mode tx, `Clearinghouse.depositCollateral` credits the full `amount` (scaled by decimals multiplier) to the user's spot balance:

```solidity
// Clearinghouse.sol:205-207
int128 amountRealized = int128(txn.amount) * int128(multiplier);
spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
``` [3](#0-2) 

The clearinghouse received `amount * (1 - f)^2` (two fee deductions) but credits `amount` — a permanent over-credit of `amount - amount*(1-f)^2`.

---

### Impact Explanation

Two distinct impacts:

1. **Solvency/accounting corruption**: The clearinghouse's on-chain token balance is less than the sum of all credited spot balances. Any user who deposits a fee-on-transfer token inflates their credited balance beyond what was actually received. When other users later withdraw, the clearinghouse will be unable to satisfy all withdrawals — a classic insolvency scenario.

2. **Cross-depositor fund theft (leg 2 drain)**: If the endpoint contract holds residual token balance from other depositors (e.g., from a concurrent deposit), leg 2 of `handleDepositTransfer` consumes those tokens to cover the shortfall, directly stealing from other users.

---

### Likelihood Explanation

Medium. The Nado protocol is a general spot/perp exchange that accepts multiple ERC20 tokens via `spotEngine.getToken(productId)`. Any token listed as a supported product that implements a transfer fee (e.g., tokens with configurable fee switches like early USDT designs, or explicitly fee-on-transfer tokens) triggers this path. The entry point `depositCollateral` and `depositCollateralWithReferral` are public and callable by any unprivileged user. No special role or governance action is required to trigger the bug — a user simply deposits a fee-bearing token.

---

### Recommendation

In `handleDepositTransfer`, measure the actual received balance after leg 1 and use that measured amount for leg 2 and for the slow-mode queue entry:

```solidity
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal returns (uint256 actualReceived) {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 before = token.balanceOf(address(this));
    safeTransferFrom(token, from, amount);
    actualReceived = token.balanceOf(address(this)) - before;
    safeTransferTo(token, address(clearinghouse), actualReceived);
}
```

Then propagate `actualReceived` (cast to `uint128`) into the `DepositCollateral` slow-mode struct instead of the caller-supplied `amount`. Apply the same pattern to `chargeSlowModeFee` if fee-bearing tokens are used for slow-mode fees.

---

### Proof of Concept

1. A fee-on-transfer token `T` with 1% fee is listed as a supported spot product.
2. Attacker calls `Endpoint.depositCollateral(subaccountName, productId, 1000e6)`.
3. `handleDepositTransfer` is invoked with `amount = 1000e6`.
   - Leg 1: `safeTransferFrom(T, attacker, endpoint, 1000e6)` → endpoint receives `990e6`.
   - Leg 2: `safeTransferTo(T, clearinghouse, 1000e6)` → attempts to send `1000e6` but endpoint only has `990e6`; either reverts or drains `10e6` from other depositors' tokens already sitting in the endpoint.
4. Assuming leg 2 succeeds by draining: clearinghouse receives `990e6` (another 1% fee) = `980.1e6`.
5. Slow-mode queue records `amount = 1000e6`.
6. Sequencer processes the tx; `Clearinghouse.depositCollateral` credits `1000e6 * multiplier` to attacker's spot balance.
7. Attacker's credited balance exceeds actual clearinghouse holdings by `~19.9e6` tokens, corrupting protocol solvency. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/EndpointStorage.sol (L95-119)
```text
    function safeTransferFrom(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal virtual {
        token.safeTransferFrom(from, address(this), amount);
    }

    function safeTransferTo(
        IERC20Base token,
        address to,
        uint256 amount
    ) internal virtual {
        token.safeTransfer(to, amount);
    }

    function handleDepositTransfer(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal {
        require(address(token) != address(0), ERR_INVALID_PRODUCT);
        safeTransferFrom(token, from, amount);
        safeTransferTo(token, address(clearinghouse), amount);
    }
```

**File:** core/contracts/Endpoint.sol (L144-148)
```text
        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
```

**File:** core/contracts/Endpoint.sol (L152-165)
```text
        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: abi.encodePacked(
                uint8(TransactionType.DepositCollateral),
                abi.encode(
                    DepositCollateral({
                        sender: subaccount,
                        productId: productId,
                        amount: amount
                    })
                )
            )
        });
```

**File:** core/contracts/Clearinghouse.sol (L193-209)
```text
    function depositCollateral(IEndpoint.DepositCollateral calldata txn)
        external
        virtual
        onlyEndpoint
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
    }
```
