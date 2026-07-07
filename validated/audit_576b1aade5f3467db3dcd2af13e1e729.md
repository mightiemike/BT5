### Title
Fee-on-Transfer Token Accounting Inflation in `depositCollateral` — (`File: core/contracts/EndpointStorage.sol`)

---

### Summary

`handleDepositTransfer` performs two sequential ERC-20 transfers using the same nominal `amount`, but never measures the actual tokens received. With a fee-on-transfer token, the protocol credits the user's on-chain balance for the full `amount` while the Clearinghouse receives strictly less, inflating every depositor's credited balance relative to real holdings and progressively draining the pool.

---

### Finding Description

`Endpoint.depositCollateralWithReferral` calls `handleDepositTransfer` with the caller-supplied `amount`: [1](#0-0) 

`handleDepositTransfer` in `EndpointStorage` executes two transfers with the same `amount`: [2](#0-1) 

- **Transfer 1** (`safeTransferFrom`): user → Endpoint. With a fee-on-transfer token the Endpoint receives `amount - fee1`.
- **Transfer 2** (`safeTransferTo`): Endpoint → Clearinghouse. The call attempts to send the original `amount`, but the Endpoint only holds `amount - fee1`. This either reverts (if no surplus balance exists) or silently drains other depositors' tokens already sitting at the Endpoint. The Clearinghouse ultimately receives `amount - fee1 - fee2`.

The slow-mode transaction queued in the same call encodes the original `amount`: [3](#0-2) 

When the sequencer later processes it, `Clearinghouse.depositCollateral` reads `txn.amount` — the original caller-supplied value — and credits the full amount to the subaccount: [4](#0-3) 

No balance-before/after check is ever performed. The `ERC20Helper.safeTransferFrom` wrapper only verifies the boolean return value, not the actual tokens received: [5](#0-4) 

The same root cause is present in the `DirectDepositV1.creditDeposit` path, which reads `token.balanceOf(address(this))` as the deposit amount and passes it directly to `depositCollateralWithReferral`: [6](#0-5) 

---

### Impact Explanation

Every deposit of a fee-on-transfer token inflates the depositor's credited balance by `fee1 + fee2` relative to what the Clearinghouse actually holds. Across many deposits this creates a growing shortfall: the sum of all credited balances exceeds the real token reserves. When users withdraw, later withdrawers cannot be paid in full, resulting in direct loss of funds for other protocol participants. The second `safeTransferTo` call can also drain tokens belonging to other depositors that are transiently held at the Endpoint address.

---

### Likelihood Explanation

The entry point (`depositCollateral` / `depositCollateralWithReferral`) is public and requires no special role. Any user can trigger it with any token that the SpotEngine has registered. While current production tokens (USDC, WETH, etc.) do not charge transfer fees, the protocol's token registry is not restricted to fee-free tokens, and fee-on-transfer behaviour can be introduced by token upgrades (as the external report notes for USDT). The `DirectDepositV1` path is additionally callable by anyone via `creditDeposit`, compounding exposure.

---

### Recommendation

Replace the fixed-`amount` two-transfer pattern with a balance-delta measurement:

```solidity
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 before = token.balanceOf(address(clearinghouse));
    safeTransferFrom(token, from, amount);
    safeTransferTo(token, address(clearinghouse), amount);
    uint256 actualReceived = token.balanceOf(address(clearinghouse)) - before;
    require(actualReceived == amount, "Fee-on-transfer token not supported");
}
```

Alternatively, whitelist only tokens that are verified to have no transfer fee, and enforce this at product registration time. The slow-mode transaction should encode `actualReceived`, not the caller-supplied `amount`, so that `Clearinghouse.depositCollateral` credits only what was truly deposited.

---

### Proof of Concept

1. A fee-on-transfer token `FTT` (1% fee per transfer) is registered as a SpotEngine product.
2. Attacker calls `Endpoint.depositCollateral(subaccountName, productId, 1000e18)`.
3. `handleDepositTransfer` runs:
   - `safeTransferFrom(FTT, attacker, Endpoint, 1000e18)` → Endpoint receives `990e18`.
   - `safeTransferTo(FTT, Clearinghouse, 1000e18)` → attempts to send `1000e18`; if Endpoint holds surplus from prior depositors it drains `10e18` of their funds; Clearinghouse receives `990e18` (another 1% fee).
4. Slow-mode tx is queued with `amount = 1000e18`.
5. Sequencer executes it; `Clearinghouse.depositCollateral` calls `spotEngine.updateBalance(productId, attacker_subaccount, 1000e18 * multiplier)`.
6. Attacker's credited balance is `1000e18` but the Clearinghouse only holds `980.1e18` of `FTT`.
7. Repeated across many depositors, the Clearinghouse becomes insolvent; the last withdrawers cannot be made whole.

### Citations

**File:** core/contracts/Endpoint.sol (L144-148)
```text
        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
```

**File:** core/contracts/Endpoint.sol (L155-164)
```text
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
```

**File:** core/contracts/EndpointStorage.sol (L111-119)
```text
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

**File:** core/contracts/Clearinghouse.sol (L205-208)
```text
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

**File:** core/contracts/DirectDepositV1.sol (L90-98)
```text
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
```
