### Title
Public `creditDepositV1()` Drains Entire DDA Balance, Rendering Owner-Only `withdrawFromDirectDepositV1()` Rescue Useless — (`core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.creditDepositV1()` carries no access control and deposits the full balance of every supported spot token held in a `DirectDepositV1` (DDA) contract into the associated subaccount. This mirrors the LooksRare pattern exactly: an owner-only rescue path (`withdrawFromDirectDepositV1`) exists to recover stuck funds, but a permissionless public function drains the same balance first, making the rescue unreachable.

---

### Finding Description

`ContractOwner` exposes two owner-gated rescue paths for funds stuck inside a DDA:

- `withdrawFromDirectDepositV1(bytes32 subaccount, address token)` — `onlyOwner`, pulls the DDA's entire balance of a given token to the owner. [1](#0-0) 

It measures `preBalance` / `postBalance` on `ContractOwner` itself and reverts with `"empty"` if nothing moved. [2](#0-1) 

In parallel, two **unrestricted** public functions exist that consume the same DDA balance:

**`ContractOwner.creditDepositV1()`** — `external`, no modifier: [3](#0-2) 

It delegates to `DirectDepositV1.creditDeposit()`, which is itself `external` with no modifier: [4](#0-3) 

`creditDeposit()` iterates every product ID returned by `spotEngine.getProductIds()`, reads `token.balanceOf(address(this))` — the **entire** current balance — approves the endpoint, and calls `depositCollateralWithReferral` for that full amount. After this call the DDA holds zero of every supported token. [5](#0-4) 

Because `withdrawFromDirectDepositV1` checks `postBalance > preBalance` and reverts on equality, a prior call to `creditDepositV1` that zeroes the DDA will cause every subsequent owner rescue attempt to revert with `"empty"`. [6](#0-5) 

---

### Impact Explanation

Any tokens accidentally sent to a DDA — by a third party, by a user who sent the wrong token, or by any other mistake — that the owner intends to recover via `withdrawFromDirectDepositV1` can be irreversibly deposited into the associated subaccount by anyone calling `creditDepositV1`. The subaccount owner receives collateral credit for tokens that did not legitimately belong to them. The owner's rescue function is permanently blocked for those tokens (balance is zero; the `"empty"` guard fires). The corrupted state is: the subaccount's on-chain collateral balance is inflated by the accidentally-received tokens, and the owner loses the ability to redirect those tokens to the rightful recipient.

---

### Likelihood Explanation

`creditDepositV1` and `DirectDepositV1.creditDeposit` are both callable by any EOA or contract with no preconditions. A subaccount owner who notices the owner's pending `withdrawFromDirectDepositV1` mempool transaction can front-run it in a single block. No special privilege, signature, or capital is required. The DDA pattern is a documented deposit flow, so DDAs holding non-trivial balances are a normal protocol state.

---

### Recommendation

Add an `onlyOwner` (or equivalent) modifier to `ContractOwner.creditDepositV1()`. Separately, consider whether `DirectDepositV1.creditDeposit()` should also be restricted to the DDA's owner (`ContractOwner`), since the DDA's `withdraw` and `withdrawNative` are already `onlyOwner`. Alternatively, track the amount of tokens deposited by the user vs. the total balance, so that `withdrawFromDirectDepositV1` can only rescue the surplus.

---

### Proof of Concept

1. User A accidentally sends 10,000 USDC to the DDA belonging to subaccount `S` (controlled by attacker B).
2. Owner observes the misdirected funds and submits `withdrawFromDirectDepositV1(S, USDC)` to return them to User A.
3. Attacker B (or any watcher) sees the pending transaction in the mempool and front-runs it by calling `ContractOwner.creditDepositV1(S)` with higher gas.
4. `creditDeposit()` reads `balanceOf(DDA) = 10,000 USDC`, approves the endpoint, and calls `depositCollateralWithReferral(S, productId, 10000e6, "-1")`. DDA balance → 0.
5. Owner's `withdrawFromDirectDepositV1` executes: `preBalance = X`, `postBalance = X` (nothing moved), reverts `"empty"`.
6. Attacker B's subaccount now holds 10,000 USDC of collateral credit. User A's funds are unrecoverable through the rescue path. [3](#0-2) [4](#0-3) [1](#0-0)

### Citations

**File:** core/contracts/ContractOwner.sol (L502-508)
```text
    function creditDepositV1(bytes32 subaccount) external {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        if (directDepositV1 == address(0)) {
            directDepositV1 = createDirectDepositV1(subaccount);
        }
        DirectDepositV1(directDepositV1).creditDeposit();
    }
```

**File:** core/contracts/ContractOwner.sol (L622-647)
```text
    function withdrawFromDirectDepositV1(bytes32 subaccount, address token)
        external
        onlyOwner
    {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        if (token == address(0)) {
            uint256 preBalance = address(this).balance;
            DirectDepositV1(directDepositV1).withdrawNative();
            uint256 postBalance = address(this).balance;
            require(postBalance > preBalance, "empty");
            (bool success, ) = msg.sender.call{value: postBalance - preBalance}(
                ""
            );
            require(success, "xfer");
        } else {
            uint256 preBalance = IERC20Base(token).balanceOf(address(this));
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(token));
            uint256 postBalance = IERC20Base(token).balanceOf(address(this));
            require(postBalance > preBalance, "empty");
            IERC20Base(token).safeTransfer(
                msg.sender,
                postBalance - preBalance
            );
        }
    }
```

**File:** core/contracts/DirectDepositV1.sol (L83-101)
```text
    function creditDeposit() external {
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0), "Invalid productId.");
            IIERC20Base token = IIERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
        }
    }
```
