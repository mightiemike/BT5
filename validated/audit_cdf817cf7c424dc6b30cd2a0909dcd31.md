### Title
Unchecked Return Value of Raw `.transferFrom()` in `replaceUsdcEWithUsdc` Enables usdcE Theft Without Providing USDC — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` is a permissionless function that uses a raw, unchecked `.transferFrom()` call to pull USDC from `msg.sender`. If that call returns `false` instead of reverting, the function continues and transfers usdcE out of the victim's Direct Deposit Account (DDA) to the caller — with no USDC ever received.

---

### Finding Description

`ContractOwner` declares `using ERC20Helper for IERC20Base` at line 24, making the safe wrapper `.safeTransferFrom()` available on every `IERC20Base` instance throughout the contract. All other token movements in the file use this safe wrapper. However, `replaceUsdcEWithUsdc` at line 616 bypasses it entirely:

```solidity
// ContractOwner.sol line 616 — raw call, return value silently discarded
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));   // sends usdcE → ContractOwner
IERC20Base(usdcE).safeTransfer(msg.sender, balance);              // sends usdcE → attacker
```

The function has **no `onlyOwner` modifier** — the only gate is `require(block.chainid == 57073, ERR_UNAUTHORIZED)`. Any unprivileged caller on chain 57073 (Ink) can invoke it. Because `ContractOwner` is the `Ownable` owner of every DDA it deploys, the subsequent `DirectDepositV1.withdraw` call succeeds unconditionally once the chain-ID check passes.

The `ERC20Helper.safeTransferFrom` wrapper (lines 23–42 of `ERC20Helper.sol`) enforces:
```solidity
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    ERR_TRANSFER_FAILED
);
```
The raw call at line 616 performs none of this validation.

---

### Impact Explanation

If the USDC token on chain 57073 returns `false` on a failed `transferFrom` (rather than reverting), an attacker with zero USDC balance can:

1. Call `replaceUsdcEWithUsdc(victimSubaccount)`.
2. The raw `transferFrom` silently returns `false`; no USDC moves.
3. `DirectDepositV1.withdraw(usdcE)` transfers the full usdcE balance of the DDA to `ContractOwner`.
4. `safeTransfer(msg.sender, balance)` forwards that usdcE to the attacker.

**Corrupted asset delta:** the entire usdcE balance of any DDA on chain 57073 is drained to an unprivileged caller. The DDA owner's collateral is permanently lost with no compensation.

---

### Likelihood Explanation

The function is permissionless on chain 57073. The trigger condition is that the USDC contract at `0x2D270e6886d130D724215A266106e6832161EAEd` returns `false` rather than reverting on a failed transfer. Non-standard or bridged USDC variants (common on newer L2s like Ink) may exhibit exactly this behavior. The attack requires no privileged access, no leaked keys, and no governance capture — only a caller with insufficient USDC allowance/balance and a token that silently fails.

---

### Recommendation

Replace the raw call with the safe wrapper already imported and in scope:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`ERC20Helper.safeTransferFrom` will revert if the transfer fails for any reason, preventing the function from continuing to drain usdcE.

---

### Proof of Concept

1. Attacker has 0 USDC and 0 allowance on chain 57073.
2. A victim DDA (`directDepositV1`) holds `N` usdcE tokens.
3. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(victimSubaccount)`.
4. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, N)` returns `false` — no revert, no USDC moved.
5. `DirectDepositV1(directDepositV1).withdraw(usdcE)` executes: `N` usdcE transferred to `ContractOwner`.
6. `IERC20Base(usdcE).safeTransfer(attacker, N)` executes: `N` usdcE transferred to attacker.
7. Attacker holds `N` usdcE; victim DDA is empty; no USDC was ever provided. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/ContractOwner.sol (L21-24)
```text
contract ContractOwner is EIP712Upgradeable, OwnableUpgradeable {
    error InvalidInput();
    using MathSD21x18 for int128;
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
