### Title
Unchecked `transferFrom()` Return Value Enables Silent Failure and usdcE Drain from DDA — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc()` performs a raw `IERC20Base(usdc).transferFrom()` call at line 616 without checking the return value. If the transfer fails silently (returns `false` rather than reverting), execution continues: the function withdraws usdcE from the target DDA and sends it to `msg.sender` — with no USDC received in exchange. The function has no access control beyond a chain ID check, making it reachable by any unprivileged caller on chain 57073.

---

### Finding Description

`replaceUsdcEWithUsdc()` is designed as a migration swap: the caller provides USDC and receives usdcE from a target subaccount's `DirectDepositV1` (DDA) contract. The three-step sequence is:

1. Pull USDC from `msg.sender` into the DDA — **raw, unchecked `transferFrom`**
2. Withdraw usdcE from the DDA to `ContractOwner`
3. Send usdcE from `ContractOwner` to `msg.sender` [1](#0-0) 

Step 1 uses a raw call:
```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```
The return value is silently discarded. Steps 2 and 3 execute unconditionally regardless of whether step 1 succeeded.

This is in direct contrast to every other transfer in the same contract and in `EndpointStorage`, which consistently use `ERC20Helper.safeTransferFrom()` / `ERC20Helper.safeTransfer()`: [2](#0-1) [3](#0-2) 

The `ERC20Helper.safeTransferFrom()` wrapper correctly handles both standard tokens (checks `bool` return) and non-standard tokens (accepts empty return data), and reverts on failure. The raw call at line 616 does neither.

---

### Impact Explanation

If the USDC `transferFrom` returns `false` instead of reverting, the caller receives the full usdcE balance of the target DDA at zero cost. The DDA's usdcE balance is completely drained. The corrupted asset delta is: DDA loses `balance` usdcE, `ContractOwner` receives nothing, `msg.sender` gains `balance` usdcE. [4](#0-3) 

---

### Likelihood Explanation

The hardcoded USDC address (`0x2D270e6886d130D724215A266106e6832161EAEd` on chain 57073) is a standard ERC-20 that reverts on failure rather than returning `false`, which limits immediate exploitability. However:

- The function has **no access control** — any unprivileged caller on chain 57073 can invoke it for any subaccount with a deployed DDA.
- If the USDC contract at that address is ever upgraded, paused, or replaced with a non-reverting implementation, the silent-failure path becomes directly exploitable.
- The pattern is a latent vulnerability: the missing return-value check is a concrete code defect, not a theoretical one. [5](#0-4) 

---

### Recommendation

Replace the raw `transferFrom` call at line 616 with the `ERC20Helper.safeTransferFrom()` wrapper already used throughout the codebase. The `using ERC20Helper for IERC20Base` directive is already active in `ContractOwner`: [6](#0-5) 

Change line 616 from:
```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```
to:
```solidity
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

This ensures the call reverts on failure for both standard and non-standard token implementations, consistent with all other transfer sites in the protocol.

---

### Proof of Concept

1. Identify a subaccount whose DDA holds a non-zero usdcE balance on chain 57073.
2. Call `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC allowance granted to `ContractOwner`.
3. The USDC `transferFrom` at line 616 returns `false` (if the token implementation does not revert on failure).
4. Execution continues: `DirectDepositV1.withdraw(usdcE)` transfers the DDA's entire usdcE balance to `ContractOwner`.
5. `ERC20Helper.safeTransfer` sends that usdcE to `msg.sender`.
6. `msg.sender` has received the DDA's usdcE balance without providing any USDC. [7](#0-6)

### Citations

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
    using ERC20Helper for IERC20Base;
```

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/EndpointStorage.sol (L88-93)
```text
        token.safeTransferFrom(
            from,
            address(this),
            clearinghouse.getSlowModeFee()
        );
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-41)
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
```
