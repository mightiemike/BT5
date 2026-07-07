### Title
Unsafe `transferFrom` Return Value Not Checked in `replaceUsdcEWithUsdc` Allows Draining DDA usdcE Without Providing USDC - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` performs a raw `.transferFrom()` call to pull USDC from the caller into a Direct Deposit Account (DDA), but does not check the boolean return value. If the USDC token returns `false` on failure instead of reverting, the caller receives usdcE from the DDA without ever providing USDC.

---

### Finding Description

`replaceUsdcEWithUsdc` is an unguarded `external` function (no `onlyOwner` modifier) callable by any address on chain 57073. Its intended logic is a token swap: the caller supplies USDC, the DDA's usdcE is withdrawn to `ContractOwner`, and then forwarded to the caller.

The critical line is:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
``` [1](#0-0) 

This is a raw `.transferFrom()` call whose boolean return value is silently discarded. The rest of the codebase consistently uses `ERC20Helper.safeTransferFrom`, which wraps the call and reverts on `false` return or call failure: [2](#0-1) 

The two subsequent operations — `DirectDepositV1.withdraw` (which uses its own `safeTransfer`) and `IERC20Base(usdcE).safeTransfer` — both execute unconditionally after the unchecked `transferFrom`: [3](#0-2) 

`DirectDepositV1.withdraw` transfers the DDA's full usdcE balance to `ContractOwner` (the caller of `withdraw`): [4](#0-3) 

`ContractOwner` then forwards that usdcE to the original `msg.sender` via `safeTransfer`. If the USDC `transferFrom` at line 616 returned `false` without reverting, the caller receives usdcE while providing nothing.

---

### Impact Explanation

An attacker who calls `replaceUsdcEWithUsdc` with a subaccount whose DDA holds usdcE can drain the DDA's entire usdcE balance without providing any USDC. The corrupted asset delta is: DDA usdcE balance → 0, attacker usdcE balance → `balance`, attacker USDC balance unchanged. This is a direct token theft from protocol-controlled DDAs.

---

### Likelihood Explanation

The function has no access control beyond `block.chainid == 57073`. Any unprivileged caller on that chain can invoke it. The trigger requires the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` to return `false` on a failed transfer (e.g., insufficient allowance) rather than reverting. Many ERC20-compatible tokens exhibit this behavior. Even if the current deployment token reverts, the missing check is a latent vulnerability that activates if the token is ever upgraded or replaced with one that follows the `false`-return pattern.

---

### Recommendation

Replace the raw `.transferFrom()` call with the project's own `ERC20Helper.safeTransferFrom`, consistent with every other transfer site in the codebase:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [5](#0-4) 

---

### Proof of Concept

1. A DDA for `subaccount` holds `N` usdcE tokens.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` on chain 57073 with zero USDC allowance granted to `ContractOwner`.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, N)` returns `false` (insufficient allowance); no USDC moves. Return value is not checked; execution continues.
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` transfers `N` usdcE from the DDA to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, N)` transfers `N` usdcE from `ContractOwner` to the attacker.
6. Attacker holds `N` usdcE; DDA holds 0 usdcE; attacker's USDC balance is unchanged. [6](#0-5)

### Citations

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

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
